package com.acdualscreen.companion.core

import java.io.IOException
import java.util.zip.CRC32

/**
 * Identifies the running game image from emulated memory alone: the game id plus a CRC of the
 * start of the main executable's code. It replaces the EmuLink "EMLKV2" handshake, whose first
 * use makes dolphin-lnk hash boot.dol by reading the disc image from its network thread: that
 * read is not thread-safe (DiscIO Blob::Read) and crashed Dolphin when it raced the game's own
 * disc reads during boot. Memory reads never touch the disc.
 */
object GameIdentity {
    /** Where GameCube DOLs load their first text section; static code once the game has booted. */
    const val CODE_ADDR = 0x80003100L
    const val CODE_LEN = 0x1000

    data class Id(val gameId: String, val fingerprint: String)

    /** The game id and fingerprint, or null when memory does not hold a game id yet. */
    @Throws(IOException::class)
    fun read(reader: MemoryReader, map: MemoryMap): Id? {
        val r = reader.read(listOf(ReadReq(map.game.idAddr, map.game.id.length), ReadReq(CODE_ADDR, CODE_LEN)))
        val idBytes = r[0] ?: return null
        if (idBytes.any { (it.toInt() and 0xFF) !in 0x20..0x7E }) return null
        val code = r[1] ?: return null
        val crc = CRC32().apply { update(code) }.value
        return Id(String(idBytes, Charsets.US_ASCII), "%08x".format(crc))
    }
}
