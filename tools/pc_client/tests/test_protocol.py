"""EmuLink wire protocol: EmuLinkClient <-> MockEmuLinkServer, and MemoryImageSource."""

import os
import socket
import struct
import tempfile
import time
import unittest

import support
from emulink import (MAX_PAYLOAD, SERVER_REPLY_BUFFER, EmuLinkClient, EmuLinkTimeout,
                     MemoryImageSource)
from mock_server import MockEmuLinkServer

IMAGE_SIZE = 1 << 20          # 1 MB test "MEM1": valid addresses 0x80000000-0x800FFFFF
END = 0x80000000 + IMAGE_SIZE


class ProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = support.pattern_image(IMAGE_SIZE)
        cls.server = MockEmuLinkServer(cls.image, port=0, game_hash="abc123").start()
        cls.client = EmuLinkClient("127.0.0.1", cls.server.port, timeout=0.3, retries=2)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.stop()

    def expect(self, addr, size):
        off = addr & 0x3FFFFFFF
        return bytes(self.image[off:off + size])

    def test_handshake(self):
        hs = self.client.handshake()
        self.assertEqual(hs["game_id"], "GAFE01")
        self.assertEqual(hs["game_hash"], "abc123")
        self.assertEqual(hs["emulator"], "dolphin")
        self.assertEqual(hs["platform"], "GCN")

    def test_single_read(self):
        self.assertEqual(self.client.read(0x80000000, 6), b"GAFE01")
        self.assertEqual(self.client.read(0x80000100, 16), self.expect(0x100, 16))
        # 0xC0000000 (uncached) mirror maps to the same physical memory
        self.assertEqual(self.client.read(0xC0000100, 16), self.expect(0x100, 16))
        self.assertEqual(self.client.read(0x80000000, 0), b"")
        self.assertEqual(len(self.client.read(0x80000000, MAX_PAYLOAD)), MAX_PAYLOAD)

    def test_read_larger_than_4096_is_chunked(self):
        before = self.server.stats["batch"]
        data = self.client.read(0x80001003, 10000)
        self.assertEqual(data, self.expect(0x1003, 10000))
        self.assertEqual(self.server.stats["batch"] - before, 1)   # 3 chunks, one packet

    def test_invalid_single_read_returns_zeros(self):
        self.assertEqual(self.client.read(END - 8, 16), bytes(16))      # crosses end of RAM
        self.assertEqual(self.client.read(0x81800000, 32), bytes(32))   # past 24 MB
        self.assertEqual(self.client.read(0x90000000, 8), bytes(8))     # MEM2 on a GameCube
        self.assertEqual(self.client.read(END - 8, 8), self.expect(IMAGE_SIZE - 8, 8))

    def test_batch_more_than_256_entries(self):
        reqs = [(0x80000000 + i * 12, 8) for i in range(600)]
        before = self.server.stats["batch"]
        out = self.client.batch_read(reqs)
        self.assertEqual(self.server.stats["batch"] - before, 3)   # 256 + 256 + 88
        self.assertEqual(out, [self.expect(a, s) for a, s in reqs])

    def test_batch_entries_larger_than_4096(self):
        reqs = [(0x80002000, 5000), (0x80004000, 4096), (0x80000011, 1), (0x80010001, 9000),
                (0x80000000, 0)]
        out = self.client.batch_read(reqs)
        self.assertEqual(out, [self.expect(a, s) for a, s in reqs])

    def test_batch_reply_stays_under_server_buffer(self):
        reqs = [(0x80000000 + i * 4096, 4096) for i in range(40)]   # 160 KB of replies
        before = self.server.stats["batch"]
        out = self.client.batch_read(reqs)
        self.assertEqual(out, [self.expect(a, s) for a, s in reqs])
        self.assertGreaterEqual(self.server.stats["batch"] - before, 3)
        self.assertLessEqual(self.server.stats["max_reply"], SERVER_REPLY_BUFFER)
        self.assertEqual(self.server.stats["overflowed_entries"], 0)

    def test_batch_invalid_entries_are_empty(self):
        reqs = [(0x80000040, 4), (END - 4, 8), (0x81800000, 4), (0x80000080, 4),
                (END - 4096, 8192), (0x80000000, 0)]
        out = self.client.batch_read(reqs)
        self.assertEqual(out[0], self.expect(0x40, 4))
        self.assertEqual(out[1], b"")
        self.assertEqual(out[2], b"")
        self.assertEqual(out[3], self.expect(0x80, 4))
        self.assertEqual(out[4], b"")          # partly invalid large request
        self.assertEqual(out[5], b"")

    def test_retry_after_lost_packet(self):
        before = self.server.stats["dropped"]
        self.server.drop_next(1)
        self.assertEqual(self.client.read(0x80000200, 8), self.expect(0x200, 8))
        self.assertEqual(self.server.stats["dropped"] - before, 1)

    def test_client_has_no_write_api(self):
        for name in ("write", "write_memory", "poke"):
            self.assertFalse(hasattr(self.client, name))

    def test_zz_client_sent_no_writes(self):
        # Runs last (alphabetical): nothing above may have produced a write packet.
        self.assertGreater(self.server.stats["single"] + self.server.stats["batch"], 0)
        self.assertEqual(self.server.stats["writes"], 0)


class ShortReplyTest(unittest.TestCase):
    """Server answers fewer entries than requested ('actual' < count)."""

    def test_remainder_is_rerequested(self):
        image = support.pattern_image(IMAGE_SIZE)
        with MockEmuLinkServer(image, port=0, reply_limit=1000, overflow="truncate") as server:
            with EmuLinkClient("127.0.0.1", server.port, timeout=0.3) as client:
                reqs = [(0x80000000 + i * 300, 200) for i in range(12)]   # ~2.4 KB of replies
                out = client.batch_read(reqs)
            self.assertEqual(out, [bytes(image[a & 0xFFFFFF:(a & 0xFFFFFF) + s]) for a, s in reqs])
            self.assertGreaterEqual(server.stats["batch"], 3)
            self.assertLessEqual(server.stats["max_reply"], 1000)


class HiccupServer(MockEmuLinkServer):
    """Delays the next requests by ``delays`` seconds each (one serving thread)."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.delays: list[float] = []

    def handle(self, packet):
        if self.delays:
            time.sleep(self.delays.pop(0))
        return super().handle(packet)


class LateReplyTest(unittest.TestCase):
    """The protocol has no request ids: a late reply must never answer a later request."""

    def test_late_reply_is_not_taken_for_the_next_request(self):
        image = support.pattern_image(IMAGE_SIZE)
        expect = lambda a, n: bytes(image[a & 0xFFFFFF:(a & 0xFFFFFF) + n])   # noqa: E731
        with HiccupServer(image, port=0) as server, \
                EmuLinkClient("127.0.0.1", server.port, timeout=0.25, retries=3) as client:
            for k in range(3):
                # Request 1 times out once (0.4 s > 0.25 s), so it is sent twice; the second
                # copy is answered 0.1 s later, after request 2 is already waiting for its reply.
                a1, a2 = 0x80001000 + k * 0x100, 0x80008000 + k * 0x100
                server.delays = [0.4, 0.1]
                self.assertEqual(client.batch_read([(a1, 8), (a1 + 4, 8)]), [expect(a1, 8), expect(a1 + 4, 8)])
                self.assertEqual(client.batch_read([(a2, 8), (a2 + 4, 8)]), [expect(a2, 8), expect(a2 + 4, 8)])
                server.delays = [0.4, 0.1]
                self.assertEqual(client.read(a1, 4), expect(a1, 4))
                self.assertEqual(client.read(a2, 4), expect(a2, 4))
            self.assertGreaterEqual(client.reconnects, 6)
            self.assertEqual(server.stats["writes"], 0)

    def test_no_reconnect_without_retries(self):
        image = support.pattern_image(IMAGE_SIZE)
        with MockEmuLinkServer(image, port=0) as server, \
                EmuLinkClient("127.0.0.1", server.port, timeout=0.5) as client:
            for k in range(20):
                client.batch_read([(0x80000000 + k * 64, 16)])
            self.assertEqual(client.reconnects, 0)
            self.assertEqual(client.transactions, 20)


class MaxReplyTest(unittest.TestCase):
    def test_small_max_reply_is_respected(self):
        image = support.pattern_image(IMAGE_SIZE)
        with MockEmuLinkServer(image, port=0) as server, \
                EmuLinkClient("127.0.0.1", server.port, timeout=0.5, max_reply=100) as client:
            out = client.batch_read([(0x80000000, 4096), (0x80002000, 50)])
            self.assertEqual(out, [bytes(image[0:4096]), bytes(image[0x2000:0x2032])])
            self.assertLessEqual(server.stats["max_reply"], 100)


class NoServerTest(unittest.TestCase):
    def test_silent_server_times_out(self):
        silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        silent.bind(("127.0.0.1", 0))
        try:
            with EmuLinkClient("127.0.0.1", silent.getsockname()[1], timeout=0.1, retries=1) as client:
                with self.assertRaises(EmuLinkTimeout):
                    client.read(0x80000000, 4)
                with self.assertRaises(EmuLinkTimeout):
                    client.handshake()
        finally:
            silent.close()

    def test_closed_port_times_out(self):
        tmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tmp.bind(("127.0.0.1", 0))
        port = tmp.getsockname()[1]
        tmp.close()
        with EmuLinkClient("127.0.0.1", port, timeout=0.1, retries=1) as client:
            with self.assertRaises(EmuLinkTimeout):
                client.batch_read([(0x80000000, 4)])


class MockServerTest(unittest.TestCase):
    def test_mock_ignores_writes(self):
        image = support.pattern_image(IMAGE_SIZE)
        original = bytes(image[0x100:0x108])
        with MockEmuLinkServer(image, port=0) as server:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.3)
            try:
                s.sendto(struct.pack("<II", 0x80000100, 8) + b"\xff" * 8, ("127.0.0.1", server.port))
                with self.assertRaises((socket.timeout, TimeoutError, ConnectionResetError)):
                    s.recvfrom(65535)
            finally:
                s.close()
            self.assertEqual(server.stats["writes"], 1)
        self.assertEqual(bytes(image[0x100:0x108]), original)

    def test_oversized_single_read_gets_no_reply(self):
        image = support.pattern_image(IMAGE_SIZE)
        server = MockEmuLinkServer(image, port=0)
        try:
            self.assertIsNone(server.handle(struct.pack("<II", 0x80000000, MAX_PAYLOAD + 1)))
            self.assertEqual(server.handle(struct.pack("<II", 0x81800000, 4)), bytes(4))
        finally:
            server.stop()


class MemoryImageSourceTest(unittest.TestCase):
    def test_dump_file_matches_protocol_semantics(self):
        image = support.pattern_image(IMAGE_SIZE)
        fd, path = tempfile.mkstemp(suffix=".raw")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(image)
            src = MemoryImageSource(path)
            self.assertEqual(src.handshake()["game_id"], "GAFE01")
            self.assertEqual(src.read(0x80000000, 6), b"GAFE01")
            self.assertEqual(src.read(0xC0000100, 16), bytes(image[0x100:0x110]))
            self.assertEqual(src.read(END - 8, 16), bytes(16))
            reqs = [(0x80000040, 4), (END - 4, 8), (0x80010001, 9000), (0x80000000, 0)]
            self.assertEqual(src.batch_read(reqs),
                             [bytes(image[0x40:0x44]), b"", bytes(image[0x10001:0x10001 + 9000]), b""])
            # Same answers as the UDP path
            with MockEmuLinkServer(image, port=0) as server, \
                    EmuLinkClient("127.0.0.1", server.port, timeout=0.3) as client:
                self.assertEqual(client.batch_read(reqs), src.batch_read(reqs))
                self.assertEqual(client.read(END - 8, 16), src.read(END - 8, 16))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
