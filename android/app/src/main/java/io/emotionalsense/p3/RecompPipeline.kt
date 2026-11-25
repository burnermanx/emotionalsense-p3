package io.emotionalsense.p3

import android.content.Context
import android.os.Build
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger
import java.util.zip.GZIPInputStream

object RecompPipeline {

    private const val KIT_FORMAT_STAMP = BuildConfig.KIT_FORMAT_STAMP

    data class AbiInfo(
        val abi: String,
        val clangTarget: String,
        val lldEmulation: String,
        val ndkTriple: String
    )

    fun detectAbi(context: Context): AbiInfo {
        val assets = context.assets.list("")?.toSet() ?: emptySet()
        for (abi in Build.SUPPORTED_ABIS) {
            if ("kit-$abi.tgz" in assets) {
                return when (abi) {
                    "x86_64" -> AbiInfo(
                        abi = "x86_64",
                        clangTarget = "x86_64-linux-android26",
                        lldEmulation = "elf_x86_64",
                        ndkTriple = "x86_64-linux-android"
                    )
                    "arm64-v8a" -> AbiInfo(
                        abi = "arm64-v8a",
                        clangTarget = "aarch64-linux-android26",
                        lldEmulation = "aarch64linux",
                        ndkTriple = "aarch64-linux-android"
                    )
                    else -> continue
                }
            }
        }
        error("No matching kit-<abi>.tgz asset for ABIs ${Build.SUPPORTED_ABIS.joinToString()}")
    }

    fun ensureKitExtracted(
        context: Context,
        log: (String) -> Unit = {},
        force: Boolean = false
    ): File {
        val abiInfo = detectAbi(context)
        val kitDir = File(context.filesDir, "kit")
        val stampFile = File(kitDir, ".kit_stamp")
        val expectedStamp = "${abiInfo.abi}-${BuildConfig.KIT_VERSION_STAMP}"

        val manifestFile = File(kitDir, "kit_manifest.json")
        val clangResDir = File(kitDir, "clang-res")
        val sysrootDir = File(kitDir, "ndk-sysroot")

        if (!force && stampFile.isFile && manifestFile.isFile && clangResDir.isDirectory && sysrootDir.isDirectory) {
            if (stampFile.readText().trim() == expectedStamp) {
                return kitDir
            }
        }

        val assetName = "kit-${abiInfo.abi}.tgz"
        log("Extracting $assetName...")
        if (kitDir.exists()) {
            kitDir.deleteRecursively()
        }
        kitDir.mkdirs()

        context.assets.open(assetName).use { raw ->
            GZIPInputStream(BufferedInputStream(raw, 65536)).use { gz ->
                extractTar(gz, kitDir)
            }
        }

        stampFile.writeText(expectedStamp)
        log("Kit extracted to ${kitDir.absolutePath}")
        return kitDir
    }

    private fun extractTar(input: java.io.InputStream, destRoot: File) {
        val hdr = ByteArray(512)
        val buf = ByteArray(65536)
        val canonicalRoot = destRoot.canonicalPath

        fun readFully(dst: ByteArray, len: Int): Int {
            var off = 0
            while (off < len) {
                val n = input.read(dst, off, len - off)
                if (n <= 0) return off
                off += n
            }
            return off
        }

        while (true) {
            val got = readFully(hdr, 512)
            if (got < 512) break
            if (hdr.all { it == 0.toByte() }) break

            val rawName = readCString(hdr, 0, 100)
            val prefix = readCString(hdr, 345, 155)
            val fullName = if (prefix.isNotEmpty()) "$prefix/$rawName" else rawName
            if (fullName.isEmpty()) break

            val modeStr = readCString(hdr, 100, 8).trim()
            val mode = modeStr.toIntOrNull(8) ?: 0
            val sizeStr = readCString(hdr, 124, 12).trim()
            val size = if (sizeStr.isEmpty()) 0L else sizeStr.toLongOrNull(8) ?: 0L
            val typeFlag = hdr[156].toInt().toChar()

            val target = File(destRoot, fullName)
            val canonicalTarget = target.canonicalPath
            if (canonicalTarget != canonicalRoot && !canonicalTarget.startsWith(canonicalRoot + File.separator)) {
                throw IOException("Blocked tar path traversal: $fullName")
            }

            if (typeFlag == '5' || fullName.endsWith("/")) {
                target.mkdirs()
            } else if (typeFlag == '0' || typeFlag == '\u0000') {
                target.parentFile?.mkdirs()
                FileOutputStream(target).use { fos ->
                    var left = size
                    while (left > 0) {
                        val want = minOf(buf.size.toLong(), left).toInt()
                        val n = readFully(buf, want)
                        if (n != want) throw IOException("Truncated tar entry: $fullName")
                        fos.write(buf, 0, n)
                        left -= n
                    }
                }
                if ((mode and 0b001001001) != 0) {
                    target.setExecutable(true, false)
                }
            } else {
                var left = size
                while (left > 0) {
                    val want = minOf(buf.size.toLong(), left).toInt()
                    val n = readFully(buf, want)
                    if (n <= 0) break
                    left -= n
                }
            }

            val rem = ((512L - (size % 512L)) % 512L).toInt()
            if (rem > 0 && typeFlag != '5' && !fullName.endsWith("/")) {
                readFully(buf, rem)
            }
        }
    }

    private fun readCString(buf: ByteArray, off: Int, maxLen: Int): String {
        var end = off
        val limit = off + maxLen
        while (end < limit && buf[end] != 0.toByte()) end++
        return String(buf, off, end - off, Charsets.UTF_8)
    }

    private fun runTool(
        cmd: List<String>,
        nativeLibDir: File,
        cwd: File? = null,
        env: Map<String, String> = emptyMap(),
        log: (String) -> Unit,
        cancelled: () -> Boolean,
        quiet: Boolean = false
    ) {
        if (!quiet) {
            log("$ " + cmd.joinToString(" "))
        }
        val pb = ProcessBuilder(cmd)
        if (cwd != null) pb.directory(cwd)
        val procEnv = pb.environment()
        procEnv["LD_LIBRARY_PATH"] = nativeLibDir.absolutePath
        if (env.isNotEmpty()) procEnv.putAll(env)
        pb.redirectErrorStream(true)
        val proc = pb.start()
        val tail = ArrayDeque<String>(40)
        proc.inputStream.bufferedReader().use { reader ->
            while (true) {
                if (cancelled()) {
                    proc.destroyForcibly()
                    throw IOException("Cancelled")
                }
                val line = reader.readLine() ?: break
                if (tail.size >= 40) tail.removeFirst()
                tail.addLast(line)
                if (!quiet) {
                    log(line)
                }
            }
        }
        val rc = proc.waitFor()
        if (rc != 0) {
            if (quiet) {
                for (l in tail) log(l)
            }
            throw IOException("${File(cmd[0]).name} failed with exit code $rc")
        }
    }

    fun getBuiltGameSo(context: Context): File =
        File(context.filesDir, "build/libemotionalsense_game.so")

    fun runFullPipeline(
        context: Context,
        isoSpec: IsoExtractor.IsoSourceSpec?,
        force: Boolean = false,
        log: (String) -> Unit,
        progress: (Float, String) -> Unit,
        cancelled: () -> Boolean
    ): File {
        val dataDir = context.filesDir
        // an interrupted rebuild must never look finished: the marker is written again only at the end
        File(dataDir, "prepared.json").delete()
        val nativeLibDir = File(context.applicationInfo.nativeLibraryDir)
        val abiInfo = detectAbi(context)

        // 1. Extract kit
        progress(0.02f, "Preparing kit")
        val kitDir = ensureKitExtracted(context, log = log, force = false)

        // 2. Extract ISO (or verify cached disc/)
        progress(0.05f, "Extracting disc")
        val extractResult = if (isoSpec != null) {
            IsoExtractor.extract(
                context = context,
                spec = isoSpec,
                dataDir = dataDir,
                force = force,
                log = log,
                progress = { f, what -> progress(0.05f + 0.15f * f, what) },
                cancelled = cancelled
            )
        } else {
            if (!IsoExtractor.isDiscReady(dataDir)) {
                throw IOException("No ISO selected and no extracted disc found in ${dataDir.absolutePath}/disc")
            }
            log("Using previously extracted disc in ${dataDir.absolutePath}/disc")
            IsoExtractor.ExtractResult(IsoExtractor.GAME_ID, IsoExtractor.GAME_TITLE, 0L, "")
        }

        if (cancelled()) throw IOException("Cancelled")

        // 3. Decrypt EBOOT.BIN (separate GPL-3.0 pspdecrypt process) + PRX relocation
        progress(0.22f, "Decrypting EBOOT.BIN")
        val moduleDir = File(dataDir, "module").apply { mkdirs() }
        val ebootBin = File(dataDir, "disc/PSP_GAME/SYSDIR/EBOOT.BIN")
        val ebootDec = File(moduleDir, "eboot_dec.elf")
        val ebootElf = File(moduleDir, "eboot.elf")

        if (force || !ebootDec.isFile || !ebootElf.isFile) {
            val pspdecrypt = File(nativeLibDir, "libpspdecrypt.so")
            val prxreloc = File(nativeLibDir, "libes_prxreloc.so")
            if (!pspdecrypt.canExecute()) throw IOException("Missing executable ${pspdecrypt.absolutePath}")
            if (!prxreloc.canExecute()) throw IOException("Missing executable ${prxreloc.absolutePath}")

            runTool(
                cmd = listOf(pspdecrypt.absolutePath, ebootBin.absolutePath, "-o", ebootDec.absolutePath),
                nativeLibDir = nativeLibDir,
                log = log,
                cancelled = cancelled
            )
            val hdr = ByteArray(4)
            ebootDec.inputStream().use { it.read(hdr) }
            if (hdr[0] != 0x7F.toByte() || hdr[1] != 'E'.code.toByte() ||
                hdr[2] != 'L'.code.toByte() || hdr[3] != 'F'.code.toByte()
            ) {
                throw IOException("pspdecrypt output is not an ELF file")
            }

            progress(0.24f, "Relocating PRX")
            runTool(
                cmd = listOf(prxreloc.absolutePath, ebootDec.absolutePath, ebootElf.absolutePath),
                nativeLibDir = nativeLibDir,
                log = log,
                cancelled = cancelled
            )
        } else {
            log("Using cached module/eboot.elf")
        }

        if (cancelled()) throw IOException("Cancelled")

        // 4. Recompile ELF -> djmax_*.c
        progress(0.26f, "Recompiling ELF to C")
        val genDir = File(dataDir, "gen")
        val existingGen = genDir.listFiles { f -> f.isFile && f.name.startsWith("djmax_") && f.name.endsWith(".c") }
        if (force || existingGen == null || existingGen.isEmpty()) {
            val recompTool = File(nativeLibDir, "liballegrexrecomp.so")
            if (!recompTool.canExecute()) throw IOException("Missing executable ${recompTool.absolutePath}")
            val workDir = File(dataDir, "work").apply { mkdirs() }
            val freshDir = File(dataDir, "gen.new")
            if (freshDir.exists()) freshDir.deleteRecursively()
            freshDir.mkdirs()

            runTool(
                cmd = listOf(recompTool.absolutePath, "funcs", ebootElf.absolutePath),
                nativeLibDir = nativeLibDir,
                cwd = workDir,
                log = log,
                cancelled = cancelled,
                quiet = true
            )
            progress(0.30f, "Emitting C files")
            runTool(
                cmd = listOf(recompTool.absolutePath, "emit", ebootElf.absolutePath, freshDir.absolutePath, "djmax"),
                nativeLibDir = nativeLibDir,
                cwd = workDir,
                log = log,
                cancelled = cancelled
            )

            genDir.mkdirs()
            val freshFiles = freshDir.listFiles()?.associateBy { it.name } ?: emptyMap()
            genDir.listFiles()?.forEach { old ->
                if (old.name !in freshFiles) old.delete()
            }
            for ((name, src) in freshFiles) {
                val dst = File(genDir, name)
                if (!dst.isFile || !dst.readBytes().contentEquals(src.readBytes())) {
                    src.copyTo(dst, overwrite = true)
                }
            }
            freshDir.deleteRecursively()
        } else {
            log("Using cached gen/ (${existingGen.size} C files)")
        }

        if (cancelled()) throw IOException("Cancelled")

        // 5. Compile djmax_*.c in parallel with libclang21.so and link with liblld.so
        progress(0.40f, "Compiling recompiled C")
        val cFiles = genDir.listFiles { f -> f.isFile && f.name.startsWith("djmax_") && f.name.endsWith(".c") }
            ?.sortedBy { it.name }
            ?: emptyList()
        if (cFiles.isEmpty()) {
            throw IOException("No djmax_*.c files generated in ${genDir.absolutePath}")
        }

        val manifest = JSONObject(File(kitDir, "kit_manifest.json").readText())
        val clangBin = manifest.optString("clang_bin", "libclang21.so")
        val lldBin = manifest.optString("lld_bin", "liblld.so")
        val clangTarget = manifest.optString("clang_target", abiInfo.clangTarget)
        val lldEmulation = manifest.optString("lld_emulation", abiInfo.lldEmulation)
        val rtBuiltinRel = manifest.optString(
            "rt_builtin",
            "clang-res/lib/linux/libclang_rt.builtins-${abiInfo.abi.removeSuffix("-v8a").let { if (it == "arm64") "aarch64" else it }}-android.a"
        )

        val clangExe = File(nativeLibDir, clangBin)
        val lldExe = File(nativeLibDir, lldBin)
        if (!clangExe.canExecute()) throw IOException("Missing executable ${clangExe.absolutePath}")
        if (!lldExe.canExecute()) throw IOException("Missing executable ${lldExe.absolutePath}")

        val clangResDir = File(kitDir, "clang-res").absolutePath
        val sysrootDir = File(kitDir, "ndk-sysroot").absolutePath
        val sysIncludeDir = "$sysrootDir/usr/include/${abiInfo.ndkTriple}"
        val kitInclude = File(kitDir, "include").absolutePath
        val genInclude = genDir.absolutePath

        val toolEnv = mapOf(
            "TMPDIR" to context.cacheDir.absolutePath,
            "HOME" to context.cacheDir.absolutePath
        )

        val buildDir = File(dataDir, "build").apply { mkdirs() }
        val objDir = File(buildDir, "obj").apply { mkdirs() }

        val jobs = Runtime.getRuntime().availableProcessors().coerceIn(2, 6)
        log("Compiling ${cFiles.size} C files with $clangBin ($jobs parallel workers)...")

        val hdrDigest = hashDirHeaders(File(kitDir, "include"))
        val compiledObjs = ArrayList<File>(cFiles.size)
        val pending = ArrayList<Triple<File, File, String>>()

        for (src in cFiles) {
            val obj = File(objDir, "${src.name}.o")
            compiledObjs.add(obj)
            val stamp = sha256Hex(src.readBytes(), hdrDigest, clangTarget.toByteArray(), KIT_FORMAT_STAMP.toByteArray())
            val shaFile = File(objDir, "${src.name}.o.sha")
            if (!force && obj.isFile && shaFile.isFile && shaFile.readText() == stamp) {
                continue
            }
            pending.add(Triple(src, obj, stamp))
        }

        val total = cFiles.size
        val doneCounter = AtomicInteger(total - pending.size)
        if (pending.isEmpty()) {
            log("All ${cFiles.size} objects up to date")
        } else {
            val pool = Executors.newFixedThreadPool(jobs)
            try {
                val tasks = pending.map { (src, obj, stamp) ->
                    Callable {
                        if (cancelled()) throw IOException("Cancelled")
                        val cmd = listOf(
                            clangExe.absolutePath,
                            "--target=$clangTarget",
                            "-resource-dir=$clangResDir",
                            "--sysroot=$sysrootDir",
                            "-isystem", sysIncludeDir,
                            "-c", "-fPIC", "-O2", "-DNDEBUG", "-std=gnu11", "-w",
                            "-I", kitInclude,
                            "-I", genInclude,
                            src.absolutePath,
                            "-o", obj.absolutePath
                        )
                        runTool(
                            cmd = cmd,
                            nativeLibDir = nativeLibDir,
                            env = toolEnv,
                            log = log,
                            cancelled = cancelled,
                            quiet = true
                        )
                        File(objDir, "${src.name}.o.sha").writeText(stamp)
                        val done = doneCounter.incrementAndGet()
                        progress(0.40f + 0.50f * (done.toFloat() / total), "Compiling $done/$total")
                    }
                }
                val futures = pool.invokeAll(tasks)
                for (f in futures) {
                    f.get()
                }
            } finally {
                pool.shutdownNow()
            }
        }

        if (cancelled()) throw IOException("Cancelled")

        // 6. Link libemotionalsense_game.so via liblld.so -flavor ld.lld
        progress(0.92f, "Linking libemotionalsense_game.so")
        val wrapsArr = manifest.getJSONArray("wrap")
        val wrapFlags = ArrayList<String>(wrapsArr.length())
        for (i in 0 until wrapsArr.length()) {
            wrapFlags.add("--wrap=${wrapsArr.getString(i)}")
        }
        val hostObjsArr = manifest.getJSONArray("host_objs")
        val hostObjs = ArrayList<String>(hostObjsArr.length())
        for (i in 0 until hostObjsArr.length()) {
            hostObjs.add(File(kitDir, hostObjsArr.getString(i)).absolutePath)
        }

        val sysrootLib = "$sysrootDir/usr/lib"
        val outSo = getBuiltGameSo(context)
        val tmpSo = File(buildDir, "libemotionalsense_game.so.tmp")
        val linkCmd = ArrayList<String>().apply {
            add(lldExe.absolutePath)
            add("-flavor")
            add("ld.lld")
            add("--eh-frame-hdr")
            add("-m")
            add(lldEmulation)
            add("-shared")
            add("-z")
            add("max-page-size=16384")
            add("-z")
            add("common-page-size=16384")
            add("--build-id=sha1")
            add("$sysrootLib/crtbegin_so.o")
            for (o in compiledObjs) add(o.absolutePath)
            addAll(hostObjs)
            add(File(kitDir, "libpsprecomp.a").absolutePath)
            add(File(nativeLibDir, "libSDL2.so").absolutePath)
            add(File(nativeLibDir, "libavcodec.so").absolutePath)
            add(File(nativeLibDir, "libavutil.so").absolutePath)
            add("-L$sysrootLib")
            addAll(wrapFlags)
            add("-lvulkan")
            add("-llog")
            add("-landroid")
            add("-lm")
            add("-ldl")
            add("-lc")
            add(File(kitDir, rtBuiltinRel).absolutePath)
            add("$sysrootLib/crtend_so.o")
            add("-o")
            add(tmpSo.absolutePath)
        }

        log("Linking ${outSo.name} with $lldBin...")
        runTool(
            cmd = linkCmd,
            nativeLibDir = nativeLibDir,
            env = toolEnv,
            log = log,
            cancelled = cancelled,
            quiet = true
        )
        if (outSo.exists()) outSo.delete()
        if (!tmpSo.renameTo(outSo)) {
            throw IOException("Failed to rename ${tmpSo.absolutePath} to ${outSo.absolutePath}")
        }

        val soSha = ModuleState.sha256File(outSo)
        val state = ModuleState.recordPrepared(context, outSo, soSha)

        val prepared = JSONObject()
            .put("version", BuildConfig.KIT_VERSION_STAMP)
            .put("module_version_code", state.preparedVersionCode)
            .put("abi", abiInfo.abi)
            .put("disc_id", extractResult.discId)
            .put("iso_sha256", extractResult.sha256)
            .put("so_size", outSo.length())
            .put("so_sha256", soSha)
        File(dataDir, "prepared.json").writeText(prepared.toString(2))

        progress(1.0f, "Ready")
        log("Built ${outSo.absolutePath} (${outSo.length() / (1024 * 1024)} MiB, v${state.preparedVersionCode})")
        return outSo
    }

    private fun hashDirHeaders(dir: File): ByteArray {
        val md = MessageDigest.getInstance("SHA-256")
        dir.walkTopDown()
            .filter { it.isFile && it.name.endsWith(".h") }
            .sortedBy { it.relativeTo(dir).invariantSeparatorsPath }
            .forEach { f ->
                md.update(f.relativeTo(dir).invariantSeparatorsPath.toByteArray())
                md.update(f.readBytes())
            }
        return md.digest()
    }

    fun sha256Hex(vararg chunks: ByteArray): String {
        val md = MessageDigest.getInstance("SHA-256")
        for (c in chunks) md.update(c)
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
