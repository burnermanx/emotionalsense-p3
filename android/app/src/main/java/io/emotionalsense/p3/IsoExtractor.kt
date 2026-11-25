package io.emotionalsense.p3

import android.content.Context
import android.net.Uri
import android.os.ParcelFileDescriptor
import org.json.JSONObject
import java.io.Closeable
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel
import java.security.MessageDigest
import java.util.zip.Inflater

object IsoExtractor {
    const val GAME_ID = "ULKS46236"
    const val GAME_TITLE = "DJMAX PORTABLE 3"
    private const val SECTOR = 2048

    private val DISC_REQUIRED = listOf(
        "PSP_GAME/PARAM.SFO",
        "PSP_GAME/SYSDIR/EBOOT.BIN",
        "PSP_GAME/INSDIR/DATA.TPA",
        "PSP_GAME/INSDIR/STREAMI.TPA",
        "PSP_GAME/INSDIR/STREAMS.TPA",
        "PSP_GAME/USRDIR/STREAMV.TPA"
    )

    data class IsoEntry(
        val path: String,
        val lba: Long,
        val size: Long,
        val isDir: Boolean
    )

    data class ExtractResult(
        val discId: String,
        val title: String,
        val imageSize: Long,
        val sha256: String
    )

    sealed class IsoSourceSpec {
        data class LocalFile(val file: File) : IsoSourceSpec()
        data class ContentUri(val uri: Uri, val displayName: String) : IsoSourceSpec()
    }

    interface RandomAccessSource : Closeable {
        val size: Long
        fun readAt(offset: Long, dst: ByteArray, dstOff: Int, len: Int): Int
    }

    private class ChannelSource(
        private val channel: FileChannel,
        private val closer: Closeable
    ) : RandomAccessSource {
        override val size: Long = channel.size()

        override fun readAt(offset: Long, dst: ByteArray, dstOff: Int, len: Int): Int {
            if (len <= 0) return 0
            var total = 0
            val buf = ByteBuffer.wrap(dst, dstOff, len)
            var pos = offset
            while (buf.hasRemaining()) {
                val n = channel.read(buf, pos)
                if (n <= 0) break
                pos += n
                total += n
            }
            return total
        }

        override fun close() {
            try {
                channel.close()
            } finally {
                closer.close()
            }
        }
    }

    private class CsoSource(private val raw: RandomAccessSource) : RandomAccessSource {
        override val size: Long
        private val blockSize: Int
        private val shift: Int
        private val isZso: Boolean
        private val index: LongArray
        private var cachedBlock = -1
        private var cachedData = ByteArray(0)

        init {
            val hdr = ByteArray(24)
            if (raw.readAt(0, hdr, 0, 24) < 24) {
                throw IOException("Truncated CSO/ZSO header")
            }
            val magic = String(hdr, 0, 4, Charsets.US_ASCII)
            if (magic != "CISO" && magic != "ZISO") {
                throw IOException("Invalid CSO/ZSO magic: $magic")
            }
            isZso = (magic == "ZISO")
            val bb = ByteBuffer.wrap(hdr).order(ByteOrder.LITTLE_ENDIAN)
            val hsize = bb.getInt(4).toLong() and 0xFFFFFFFFL
            val total = bb.getLong(8)
            val bsize = bb.getInt(16)
            val ver = hdr[20].toInt() and 0xFF
            val sh = hdr[21].toInt() and 0xFF
            if ((hsize != 0L && hsize != 24L) || ver > 2 || bsize < 512 || (bsize and (bsize - 1)) != 0 || sh > 8 || total <= 0L) {
                throw IOException("Invalid CSO/ZSO parameters: ver=$ver bsize=$bsize shift=$sh")
            }
            size = total
            blockSize = bsize
            shift = sh
            val nblocks = ((total + bsize - 1L) / bsize).toInt()
            val idxBytes = ByteArray(4 * (nblocks + 1))
            if (raw.readAt(24, idxBytes, 0, idxBytes.size) < idxBytes.size) {
                throw IOException("Truncated CSO/ZSO index")
            }
            val ibb = ByteBuffer.wrap(idxBytes).order(ByteOrder.LITTLE_ENDIAN)
            index = LongArray(nblocks + 1) { ibb.getInt(it * 4).toLong() and 0xFFFFFFFFL }
        }

        private fun loadBlock(bn: Int): ByteArray {
            if (bn == cachedBlock) return cachedData
            val a = index[bn]
            val b = index[bn + 1]
            val start = (a and 0x7FFFFFFFL) shl shift
            val end = (b and 0x7FFFFFFFL) shl shift
            if (end < start || end > raw.size) {
                throw IOException("Corrupt CSO/ZSO block $bn ($start..$end)")
            }
            val compLen = (end - start).toInt()
            val comp = ByteArray(compLen)
            if (raw.readAt(start, comp, 0, compLen) < compLen) {
                throw IOException("Short read on CSO/ZSO block $bn")
            }
            val want = minOf(blockSize.toLong(), size - bn.toLong() * blockSize).toInt()
            val blk = if ((a and 0x80000000L) != 0L) {
                comp.copyOf(want)
            } else if (isZso) {
                lz4Block(comp, want)
            } else {
                inflateRaw(comp, want)
            }
            if (blk.size < want) {
                throw IOException("Short decompressed CSO/ZSO block $bn")
            }
            cachedBlock = bn
            cachedData = blk
            return blk
        }

        override fun readAt(offset: Long, dst: ByteArray, dstOff: Int, len: Int): Int {
            if (offset >= size || len <= 0) return 0
            val maxRead = minOf(len.toLong(), size - offset).toInt()
            var pos = offset
            var outPos = dstOff
            var remaining = maxRead
            while (remaining > 0) {
                val bn = (pos / blockSize).toInt()
                val offInBlk = (pos % blockSize).toInt()
                val blk = loadBlock(bn)
                val take = minOf(blockSize - offInBlk, remaining)
                System.arraycopy(blk, offInBlk, dst, outPos, take)
                pos += take
                outPos += take
                remaining -= take
            }
            return maxRead
        }

        override fun close() {
            raw.close()
        }
    }

    private fun inflateRaw(src: ByteArray, size: Int): ByteArray {
        val inf = Inflater(true)
        try {
            inf.setInput(src)
            val out = ByteArray(size)
            var off = 0
            while (!inf.finished() && off < size) {
                val n = inf.inflate(out, off, size - off)
                if (n == 0) {
                    if (inf.needsInput() || inf.needsDictionary()) break
                }
                off += n
            }
            if (off < size) throw IOException("Truncated deflate block ($off < $size)")
            return out
        } finally {
            inf.end()
        }
    }

    private fun lz4Block(src: ByteArray, size: Int): ByteArray {
        val out = ByteArray(size)
        var op = 0
        var ip = 0
        val n = src.size
        while (ip < n && op < size) {
            val token = src[ip++].toInt() and 0xFF
            var lit = token ushr 4
            if (lit == 15) {
                while (true) {
                    if (ip >= n) throw IOException("LZ4 truncated literal length")
                    val b = src[ip++].toInt() and 0xFF
                    lit += b
                    if (b != 255) break
                }
            }
            if (ip + lit > n) throw IOException("LZ4 truncated literals")
            val litCopy = minOf(lit, size - op)
            System.arraycopy(src, ip, out, op, litCopy)
            ip += lit
            op += litCopy
            if (op >= size || ip >= n) break
            if (ip + 2 > n) throw IOException("LZ4 truncated offset")
            val off = (src[ip].toInt() and 0xFF) or ((src[ip + 1].toInt() and 0xFF) shl 8)
            ip += 2
            if (off == 0 || off > op) throw IOException("LZ4 bad match offset $off")
            var mlen = token and 0x0F
            if (mlen == 15) {
                while (true) {
                    if (ip >= n) throw IOException("LZ4 truncated match length")
                    val b = src[ip++].toInt() and 0xFF
                    mlen += b
                    if (b != 255) break
                }
            }
            mlen += 4
            val matchEnd = minOf(op + mlen, size)
            while (op < matchEnd) {
                out[op] = out[op - off]
                op++
            }
        }
        if (op < size) throw IOException("LZ4 short block ($op < $size)")
        return out
    }

    private fun openRawSource(context: Context, spec: IsoSourceSpec): RandomAccessSource {
        val raw: RandomAccessSource = when (spec) {
            is IsoSourceSpec.LocalFile -> {
                val raf = RandomAccessFile(spec.file, "r")
                ChannelSource(raf.channel, raf)
            }
            is IsoSourceSpec.ContentUri -> {
                val pfd: ParcelFileDescriptor = context.contentResolver.openFileDescriptor(spec.uri, "r")
                    ?: throw IOException("Cannot open URI: ${spec.uri}")
                val fis = FileInputStream(pfd.fileDescriptor)
                ChannelSource(fis.channel, Closeable {
                    try {
                        fis.close()
                    } finally {
                        pfd.close()
                    }
                })
            }
        }
        val magic = ByteArray(4)
        val n = raw.readAt(0, magic, 0, 4)
        if (n == 4) {
            val tag = String(magic, Charsets.US_ASCII)
            if (tag == "CISO" || tag == "ZISO") {
                return CsoSource(raw)
            }
        }
        return raw
    }

    private fun readFully(src: RandomAccessSource, offset: Long, len: Int): ByteArray {
        val out = ByteArray(len)
        val got = src.readAt(offset, out, 0, len)
        if (got < len) throw IOException("Short read at offset $offset ($got < $len)")
        return out
    }

    private fun u32le(buf: ByteArray, off: Int): Long {
        return (buf[off].toLong() and 0xFFL) or
            ((buf[off + 1].toLong() and 0xFFL) shl 8) or
            ((buf[off + 2].toLong() and 0xFFL) shl 16) or
            ((buf[off + 3].toLong() and 0xFFL) shl 24)
    }

    private fun u16le(buf: ByteArray, off: Int): Int {
        return (buf[off].toInt() and 0xFF) or ((buf[off + 1].toInt() and 0xFF) shl 8)
    }

    fun parseSfo(data: ByteArray): Map<String, String> {
        if (data.size < 20 || data[0] != 0.toByte() || data[1] != 'P'.code.toByte() ||
            data[2] != 'S'.code.toByte() || data[3] != 'F'.code.toByte()
        ) {
            return emptyMap()
        }
        val keyTab = u32le(data, 8).toInt()
        val dataTab = u32le(data, 12).toInt()
        val count = u32le(data, 16).toInt()
        val out = LinkedHashMap<String, String>()
        for (i in 0 until count) {
            val off = 20 + i * 16
            if (off + 16 > data.size) break
            val ko = u16le(data, off)
            val fmt = u16le(data, off + 2)
            val ln = u32le(data, off + 4).toInt()
            val doff = u32le(data, off + 12).toInt()
            val kStart = keyTab + ko
            var kEnd = kStart
            while (kEnd < data.size && data[kEnd] != 0.toByte()) kEnd++
            val key = String(data, kStart, kEnd - kStart, Charsets.UTF_8)
            val vStart = dataTab + doff
            val vEnd = minOf(vStart + ln, data.size)
            if (fmt == 0x0404 && vStart + 4 <= data.size) {
                out[key] = u32le(data, vStart).toString()
            } else if (vStart <= vEnd) {
                var trimmedEnd = vEnd
                while (trimmedEnd > vStart && data[trimmedEnd - 1] == 0.toByte()) trimmedEnd--
                out[key] = String(data, vStart, trimmedEnd - vStart, Charsets.UTF_8)
            }
        }
        return out
    }

    private fun walkIso(src: RandomAccessSource): List<IsoEntry> {
        val pvd = readFully(src, 16L * SECTOR, SECTOR)
        if (pvd[0].toInt() != 1 || String(pvd, 1, 5, Charsets.US_ASCII) != "CD001") {
            throw IOException("Not an ISO9660 disc image")
        }
        val rootLba = u32le(pvd, 156 + 2)
        val rootSize = u32le(pvd, 156 + 10)
        val out = ArrayList<IsoEntry>()
        val seen = HashSet<Long>()

        fun rec(lba: Long, size: Long, prefix: String) {
            if (!seen.add(lba)) return
            val data = readFully(src, lba * SECTOR, size.toInt())
            var pos = 0
            while (pos < data.size) {
                val ln = data[pos].toInt() and 0xFF
                if (ln == 0) {
                    pos = ((pos / SECTOR) + 1) * SECTOR
                    continue
                }
                if (pos + ln > data.size) break
                val eLba = u32le(data, pos + 2)
                val eSize = u32le(data, pos + 10)
                val flags = data[pos + 25].toInt() and 0xFF
                val nlen = data[pos + 32].toInt() and 0xFF
                if (pos + 33 + nlen <= data.size) {
                    val isSpecial = nlen == 1 && (data[pos + 33] == 0.toByte() || data[pos + 33] == 1.toByte())
                    if (!isSpecial) {
                        val rawName = String(data, pos + 33, nlen, Charsets.US_ASCII)
                        val cleanName = rawName.substringBefore(';')
                        val fullPath = "$prefix$cleanName"
                        val isDir = (flags and 2) != 0
                        out.add(IsoEntry(fullPath, eLba, eSize, isDir))
                        if (isDir) {
                            rec(eLba, eSize, "$fullPath/")
                        }
                    }
                }
                pos += ln
            }
        }

        rec(rootLba, rootSize, "")
        return out
    }

    fun checkDiscTree(discDir: File): String? {
        for (rel in DISC_REQUIRED) {
            val f = File(discDir, rel)
            if (!f.isFile || f.length() == 0L) {
                return "missing or empty: $rel"
            }
        }
        val sfo = parseSfo(File(discDir, "PSP_GAME/PARAM.SFO").readBytes())
        val discId = sfo["DISC_ID"] ?: "?"
        if (discId != GAME_ID) {
            return "another game: $discId"
        }
        return null
    }

    fun isDiscReady(dataDir: File): Boolean {
        val marker = File(dataDir, ".extracted.json")
        if (!marker.isFile) return false
        return checkDiscTree(File(dataDir, "disc")) == null
    }

    fun extract(
        context: Context,
        spec: IsoSourceSpec,
        dataDir: File,
        force: Boolean = false,
        log: (String) -> Unit,
        progress: (Float, String) -> Unit,
        cancelled: () -> Boolean
    ): ExtractResult {
        val discDir = File(dataDir, "disc")
        val marker = File(dataDir, ".extracted.json")
        val msDir = File(dataDir, "ms")
        msDir.mkdirs()

        openRawSource(context, spec).use { src ->
            val entries = walkIso(src)
            val byPath = entries.associateBy { it.path }
            val sfoEntry = byPath["PSP_GAME/PARAM.SFO"]
                ?: throw IOException("Missing PSP_GAME/PARAM.SFO in disc image")
            if ("PSP_GAME/SYSDIR/EBOOT.BIN" !in byPath) {
                throw IOException("Missing PSP_GAME/SYSDIR/EBOOT.BIN in disc image")
            }
            val sfo = parseSfo(readFully(src, sfoEntry.lba * SECTOR, sfoEntry.size.toInt()))
            val discId = sfo["DISC_ID"] ?: "?"
            val title = sfo["TITLE"] ?: "?"
            log("ISO: $title [$discId] (${src.size / (1024 * 1024)} MiB)")
            if (discId != GAME_ID && !force) {
                throw IOException("Expected $GAME_TITLE [$GAME_ID], found $title [$discId]")
            }

            // Compute a fast fingerprint or full SHA-256? Check if marker matches size + name + mtime first,
            // or hash the SFO + EBOOT + size, or full stream if needed. Hashing full 1.7 GiB ISO takes time,
            // so if marker already exists and checkDiscTree passes with matching imageSize & ebootSha, reuse!
            val ebootEntry = byPath["PSP_GAME/SYSDIR/EBOOT.BIN"]!!
            val ebootBytes = readFully(src, ebootEntry.lba * SECTOR, ebootEntry.size.toInt())
            val md = MessageDigest.getInstance("SHA-256")
            md.update(ebootBytes)
            md.update(src.size.toString().toByteArray())
            val quickDigest = md.digest().joinToString("") { "%02x".format(it) }

            if (!force && marker.isFile && checkDiscTree(discDir) == null) {
                try {
                    val prev = JSONObject(marker.readText())
                    if (prev.optString("sha256") == quickDigest && prev.optLong("size") == src.size) {
                        log("Disc already extracted in ${discDir.absolutePath}")
                        progress(1f, "Cached")
                        return ExtractResult(discId, title, src.size, quickDigest)
                    }
                } catch (_: Exception) {
                }
            }

            val filesToExtract = entries.filter {
                !it.isDir && (it.path == "UMD_DATA.BIN" || it.path.startsWith("PSP_GAME/"))
            }
            val totalBytes = filesToExtract.sumOf { it.size }.coerceAtLeast(1L)
            var doneBytes = 0L
            val copyBuf = ByteArray(4 * 1024 * 1024)

            for (entry in filesToExtract) {
                if (cancelled()) throw IOException("Cancelled")
                val dest = File(discDir, entry.path)
                if (dest.isFile && dest.length() == entry.size) {
                    doneBytes += entry.size
                    progress(doneBytes.toFloat() / totalBytes, entry.path)
                    continue
                }
                dest.parentFile?.mkdirs()
                val tmp = File(dest.parentFile, dest.name + ".part")
                FileOutputStream(tmp).use { fos ->
                    var left = entry.size
                    var off = entry.lba * SECTOR
                    while (left > 0) {
                        if (cancelled()) throw IOException("Cancelled")
                        val chunk = minOf(copyBuf.size.toLong(), left).toInt()
                        val got = src.readAt(off, copyBuf, 0, chunk)
                        if (got <= 0) throw IOException("Short read extracting ${entry.path}")
                        fos.write(copyBuf, 0, got)
                        off += got
                        left -= got
                        doneBytes += got
                        progress(doneBytes.toFloat() / totalBytes, entry.path)
                    }
                }
                if (dest.exists()) dest.delete()
                if (!tmp.renameTo(dest)) {
                    throw IOException("Failed to rename ${tmp.absolutePath} to ${dest.absolutePath}")
                }
            }

            val treeErr = checkDiscTree(discDir)
            if (treeErr != null) {
                throw IOException("Extracted disc incomplete: $treeErr")
            }

            marker.writeText(
                JSONObject()
                    .put("sha256", quickDigest)
                    .put("size", src.size)
                    .put("disc_id", discId)
                    .toString()
            )
            progress(1f, "Extracted")
            return ExtractResult(discId, title, src.size, quickDigest)
        }
    }
}
