"""Read-only client for the dolphin-lnk EmuLink UDP protocol (EMLKV2).

Wire format (see dolphin-lnk Source/Core/Core/EmuLinkServer.cpp):

* Handshake: send the 6 bytes ``EMLKV2``; the reply is a JSON object
  ``{"emulator", "game_id", "game_hash", "platform"}``.
* Single read: send 8 bytes ``<u32 addr><u32 size>`` (little-endian).  The reply
  is exactly ``size`` raw bytes (size <= 4096).  An invalid range is answered
  with ``size`` zero bytes.
* Batch read: ``'E' 'L' <u16 count>`` then ``count`` x ``<u32 addr><u32 size>``
  (all little-endian, 1 <= count <= 256).  The reply is ``'E' 'L' <u16 actual>``
  followed by ``actual`` x ``<u16 len><len bytes>``.  Invalid entries come back
  with len 0.  The server's reply buffer is 65000 bytes.

Game data itself is big-endian; this module only moves bytes around.

This module deliberately has no write support: the server also accepts write
packets (8-byte header + data) and we never build one.

Two sources share the same interface (``handshake``, ``read``, ``batch_read``,
``describe``, ``close``):

* :class:`EmuLinkClient` talks UDP to a running emulator.
* :class:`MemoryImageSource` serves the same calls from a MEM1 dump
  (Dolphin's ``Dump MRAM`` -> ``mem1.raw``) or from bytes in memory.
"""

from __future__ import annotations

import hashlib
import json
import socket
import struct
import time
from typing import Iterable, Sequence

DEFAULT_PORT = 55355
HANDSHAKE_MAGIC = b"EMLKV2"
BATCH_MAGIC = b"EL"
MAX_PAYLOAD = 4096        # largest single read / batch entry the server serves
MAX_BATCH_ENTRIES = 256   # server rejects count > 256
SERVER_REPLY_BUFFER = 65000
MEM1_SIZE = 24 * 1024 * 1024
PHYS_MASK = 0x3FFFFFFF


class EmuLinkError(Exception):
    """Protocol or transport failure."""


class EmuLinkTimeout(EmuLinkError):
    """No (valid) reply after all retries -- emulator not running or no game booted."""


# --------------------------------------------------------------------------- #
# Memory image (shared by MemoryImageSource and mock_server)
# --------------------------------------------------------------------------- #

class MemoryImage:
    """A MEM1 byte image with the same address rules as the EmuLink server.

    ``phys = addr & 0x3FFFFFFF``; a range is valid when ``size > 0`` and
    ``phys + size <= len(image)``.  For MEM1 addresses this is the same as
    ``offset = addr & 0x01FFFFFF`` into ``mem1.raw``, but it also rejects
    addresses outside MEM1 instead of aliasing them.
    """

    def __init__(self, data: bytes | bytearray):
        self.data = data

    def check(self, addr: int, size: int) -> int | None:
        """Return the physical offset for a valid range, else None."""
        phys = addr & PHYS_MASK
        end = (phys + size) & 0xFFFFFFFF   # the server does this sum in u32
        if end <= phys or end > len(self.data):
            return None
        return phys

    def read_or_zeros(self, addr: int, size: int) -> bytes:
        """Single-read semantics: invalid ranges read as zeros."""
        if size <= 0:
            return b""
        phys = self.check(addr, size)
        if phys is None:
            return bytes(size)
        return bytes(self.data[phys:phys + size])

    def read_or_empty(self, addr: int, size: int) -> bytes:
        """Batch-entry semantics: invalid ranges read as b''."""
        phys = self.check(addr, size)
        if phys is None:
            return b""
        return bytes(self.data[phys:phys + size])


# --------------------------------------------------------------------------- #
# UDP client
# --------------------------------------------------------------------------- #

class EmuLinkClient:
    """Read-only EmuLink client.

    ``timeout`` is per attempt; each request is tried ``retries + 1`` times.
    ``max_reply`` caps the size of each batch reply so the server's 65000-byte
    buffer never overflows (an overflowing entry would be reported as len 0,
    which is indistinguishable from an invalid address).

    The protocol has no request ids, so a late reply to an earlier request
    could be mistaken for the answer to a new one with the same shape.  Within
    one transaction every attempt sends the same packet, so any reply is fine.
    But once a transaction has sent more than one packet (or failed), replies
    may still be in flight, and the next transaction uses a fresh socket (a
    new source port): those late replies then go to the closed one.

    Counters: ``transactions`` (request/reply exchanges; retries count once),
    ``packets_sent`` and ``reconnects`` (sockets replaced).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 timeout: float = 0.5, retries: int = 2,
                 max_reply: int = SERVER_REPLY_BUFFER):
        if max_reply < 4 + 2 + 1:
            raise ValueError("max_reply too small")
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.max_reply = max_reply
        self.packets_sent = 0
        self.transactions = 0
        self.reconnects = 0
        # The server binds IPv4 "any", so resolve the host as IPv4.
        self._addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        self._sock: socket.socket | None = None
        self._stale = False   # replies to an earlier transaction may still arrive
        self._open_socket()

    def _open_socket(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self.reconnects += 1
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:
            pass
        # connect() makes recv() ignore datagrams from anyone but the server.
        sock.connect(self._addr)
        self._sock = sock
        self._stale = False

    # -- public API -------------------------------------------------------

    def describe(self) -> str:
        return f"udp://{self.host}:{self.port}"

    def close(self) -> None:
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def handshake(self) -> dict:
        def validate(data: bytes):
            if not data.startswith(b"{"):
                return None
            try:
                obj = json.loads(data.decode("utf-8", "replace"))
            except ValueError:
                return None
            return obj if isinstance(obj, dict) and "game_id" in obj else None

        return self._transact(HANDSHAKE_MAGIC, validate, "handshake")

    def read(self, addr: int, size: int) -> bytes:
        """Read ``size`` bytes.  Invalid ranges read as zeros (server semantics)."""
        addr &= 0xFFFFFFFF
        if size <= 0:
            return b""
        if size <= MAX_PAYLOAD:
            packet = struct.pack("<II", addr, size)
            return self._transact(packet, lambda d: d if len(d) == size else None,
                                  f"read 0x{addr:08X}+{size}")
        data = self.batch_read([(addr, size)])[0]
        return data if data else bytes(size)

    def batch_read(self, requests: Iterable[tuple[int, int]]) -> list[bytes]:
        """Read many ranges.  Returns one bytes object per request, b'' if invalid.

        Requests larger than 4096 bytes are split into chunks and reassembled;
        if any chunk is invalid the whole request returns b''.  Requests are
        packed into as few packets as possible (<= 256 entries and a reply of
        <= ``max_reply`` bytes each).
        """
        reqs = [(int(a) & 0xFFFFFFFF, int(s)) for a, s in requests]
        # A piece must fit a reply on its own: 'EL' + count + len + data.
        piece_max = min(MAX_PAYLOAD, self.max_reply - 6)
        pieces: list[tuple[int, int, int]] = []   # (request index, addr, size)
        for i, (addr, size) in enumerate(reqs):
            if size < 0:
                raise ValueError(f"negative size for request {i}")
            off = 0
            while off < size:
                n = min(piece_max, size - off)
                pieces.append((i, (addr + off) & 0xFFFFFFFF, n))
                off += n

        results: list[bytes] = [b""] * len(pieces)
        pos = 0
        while pos < len(pieces):
            group: list[int] = []
            reply_len = 4
            j = pos
            while j < len(pieces) and len(group) < MAX_BATCH_ENTRIES:
                need = 2 + pieces[j][2]
                if group and reply_len + need > self.max_reply:
                    break
                group.append(j)
                reply_len += need
                j += 1
            entries = [(pieces[k][1], pieces[k][2]) for k in group]
            datas = self._batch_packet(entries)
            # The server may answer fewer entries than asked ('actual' < count)
            # if its reply buffer filled up; the rest is re-requested next loop.
            for k, data in zip(group, datas):
                results[k] = data
            pos += len(datas)

        out = [bytearray() for _ in reqs]
        bad: set[int] = set()
        for (i, _addr, size), data in zip(pieces, results):
            if len(data) != size:
                bad.add(i)
            else:
                out[i] += data
        return [b"" if i in bad else bytes(buf) for i, buf in enumerate(out)]

    # -- internals ----------------------------------------------------------

    def _batch_packet(self, entries: Sequence[tuple[int, int]]) -> list[bytes]:
        count = len(entries)
        packet = bytearray(BATCH_MAGIC + struct.pack("<H", count))
        for addr, size in entries:
            packet += struct.pack("<II", addr, size)

        def validate(data: bytes):
            if len(data) < 4 or data[:2] != BATCH_MAGIC:
                return None
            actual = struct.unpack_from("<H", data, 2)[0]
            if actual == 0 or actual > count:
                return None
            off = 4
            out = []
            for idx in range(actual):
                if off + 2 > len(data):
                    return None
                n = struct.unpack_from("<H", data, off)[0]
                off += 2
                if n not in (0, entries[idx][1]) or off + n > len(data):
                    return None   # not a reply to this request (stale) or corrupt
                out.append(bytes(data[off:off + n]))
                off += n
            return out if off == len(data) else None

        return self._transact(bytes(packet), validate, f"batch of {count}")

    def _drain(self) -> None:
        """Discard stray datagrams (e.g. duplicates) before a new transaction."""
        self._sock.setblocking(False)
        try:
            while True:
                try:
                    self._sock.recv(65535)
                except (BlockingIOError, InterruptedError):
                    break
                except ConnectionResetError:
                    continue   # Windows: ICMP port-unreachable from an earlier send
                except OSError:
                    break
        finally:
            self._sock.setblocking(True)

    def _transact(self, packet: bytes, validate, what: str):
        self.transactions += 1
        if self._stale:
            self._open_socket()   # see the class docstring
        self._drain()
        self._stale = True        # until a reply to our only packet is accepted
        sent = 0
        last_error = "no reply"
        for _attempt in range(self.retries + 1):
            # No drain between attempts: every attempt sends the same packet,
            # so a late reply to an earlier attempt is still a valid answer.
            try:
                self._sock.send(packet)
                self.packets_sent += 1
                sent += 1
            except OSError as exc:
                last_error = f"send failed: {exc}"
                continue
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._sock.settimeout(remaining)
                try:
                    data = self._sock.recv(65535)
                except (socket.timeout, TimeoutError):
                    break
                except ConnectionResetError:
                    # Windows reports ICMP port-unreachable this way: nothing
                    # is listening.  Count it as a failed attempt.
                    last_error = "connection refused (no server listening)"
                    break
                except OSError as exc:
                    last_error = str(exc)
                    break
                result = validate(data)
                if result is not None:
                    self._stale = sent > 1   # other attempts may still be answered
                    return result
                last_error = "unexpected reply"
        raise EmuLinkTimeout(f"{what}: {last_error} from {self.describe()} "
                             f"after {self.retries + 1} attempt(s)")


# --------------------------------------------------------------------------- #
# Dump-file source
# --------------------------------------------------------------------------- #

class MemoryImageSource:
    """Serve reads from a MEM1 dump file (``mem1.raw``) or from bytes.

    ``path`` may be a filesystem path or a bytes/bytearray image.  The handshake
    reports the game id found at 0x80000000 and an md5 of the image as the hash
    (a stand-in for the server's boot.dol hash; it only needs to change when
    the image changes).
    """

    def __init__(self, path, game_hash: str | None = None, platform: str = "GCN"):
        if isinstance(path, (bytes, bytearray, memoryview)):
            data = path if isinstance(path, (bytes, bytearray)) else bytes(path)   # no copy: live edits show
            self.path = None
        else:
            with open(path, "rb") as fh:
                data = fh.read()
            self.path = str(path)
        self.image = MemoryImage(data)
        self.platform = platform
        self.game_hash = game_hash or hashlib.md5(data, usedforsecurity=False).hexdigest()

    def describe(self) -> str:
        return f"dump:{self.path}" if self.path else "dump:<memory>"

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def handshake(self) -> dict:
        raw = self.image.read_or_zeros(0x80000000, 6)
        game_id = raw.split(b"\0", 1)[0].decode("ascii", "replace")
        return {"emulator": "dump", "game_id": game_id,
                "game_hash": self.game_hash, "platform": self.platform}

    def read(self, addr: int, size: int) -> bytes:
        return self.image.read_or_zeros(addr & 0xFFFFFFFF, size)

    def batch_read(self, requests: Iterable[tuple[int, int]]) -> list[bytes]:
        return [self.image.read_or_empty(int(a) & 0xFFFFFFFF, int(s)) if int(s) > 0 else b""
                for a, s in requests]
