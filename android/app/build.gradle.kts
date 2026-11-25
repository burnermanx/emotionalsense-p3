import java.io.BufferedOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.charset.StandardCharsets
import java.util.zip.GZIPOutputStream
import java.util.zip.ZipFile

plugins {
    id("com.android.application")
}

val repoRoot = rootProject.projectDir.parentFile
val stageRoot = File(repoRoot, "build-android/stage")
val requestedAbis = providers.gradleProperty("androidAbis").orNull
    ?.split(",")
    ?.map { it.trim() }
    ?.filter { it.isNotEmpty() }
val availableAbis = listOf("x86_64", "arm64-v8a").filter { abi ->
    File(stageRoot, "$abi/jniLibs/$abi").isDirectory &&
        File(stageRoot, "$abi/kit/$abi").isDirectory
}
val supportedAbis = (requestedAbis ?: availableAbis).ifEmpty { listOf("x86_64") }

val generatedKitAssetsDir = layout.buildDirectory.dir("generated/assets/kit")
val generatedModuleAssetsDir = layout.buildDirectory.dir("generated/assets/gamemodule")

android {
    namespace = "io.emotionalsense.p3"
    compileSdk = 37

    defaultConfig {
        applicationId = "io.emotionalsense.p3"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.2.0-dev"

        ndk {
            abiFilters += supportedAbis
        }

        buildConfigField("String", "GAME_MODULE_PACKAGE", "\"io.emotionalsense.p3.gamemodule\"")
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    androidResources {
        noCompress += listOf("tgz", "bin")
    }

    packaging {
        jniLibs {
            useLegacyPackaging = true
            keepDebugSymbols += listOf("**/*.so")
        }
    }

    sourceSets {
        getByName("main") {
            java.directories.add(File(stageRoot, "sdl-java").absolutePath)
            jniLibs.directories.addAll(supportedAbis.map { File(stageRoot, "$it/jniLibs").absolutePath })
            assets.directories.add(generatedKitAssetsDir.get().asFile.absolutePath)
            assets.directories.add(generatedModuleAssetsDir.get().asFile.absolutePath)
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
}

dependencies {
    implementation("com.android.tools.build:apksig:9.1.1")
}

abstract class PackageKitTask : DefaultTask() {
    @get:InputDirectory
    abstract val stageDir: DirectoryProperty

    @get:Input
    abstract val abis: ListProperty<String>

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    private fun writeTarHeader(out: BufferedOutputStream, name: String, size: Long, isDir: Boolean, executable: Boolean) {
        val hdr = ByteArray(512)
        val nameBytes = name.toByteArray(StandardCharsets.UTF_8)
        require(nameBytes.size <= 100) { "Tar path too long: $name" }
        System.arraycopy(nameBytes, 0, hdr, 0, nameBytes.size)

        val mode = if (isDir || executable) "0000755\u0000" else "0000644\u0000"
        System.arraycopy(mode.toByteArray(StandardCharsets.US_ASCII), 0, hdr, 100, 8)
        System.arraycopy("0000000\u0000".toByteArray(StandardCharsets.US_ASCII), 0, hdr, 108, 8)
        System.arraycopy("0000000\u0000".toByteArray(StandardCharsets.US_ASCII), 0, hdr, 116, 8)

        val sizeStr = String.format("%011o\u0000", if (isDir) 0L else size)
        System.arraycopy(sizeStr.toByteArray(StandardCharsets.US_ASCII), 0, hdr, 124, 12)
        System.arraycopy("00000000000\u0000".toByteArray(StandardCharsets.US_ASCII), 0, hdr, 136, 12)
        for (i in 148 until 156) hdr[i] = ' '.code.toByte()
        hdr[156] = if (isDir) '5'.code.toByte() else '0'.code.toByte()
        System.arraycopy("ustar\u000000".toByteArray(StandardCharsets.US_ASCII), 0, hdr, 257, 8)

        var csum = 0
        for (b in hdr) csum += (b.toInt() and 0xFF)
        val csumStr = String.format("%06o\u0000 ", csum)
        System.arraycopy(csumStr.toByteArray(StandardCharsets.US_ASCII), 0, hdr, 148, 8)
        out.write(hdr)
    }

    private fun addTree(out: BufferedOutputStream, baseDir: File, prefix: String, filter: (String) -> Boolean) {
        val files = baseDir.walkTopDown()
            .onEnter { dir ->
                val rel = dir.relativeTo(baseDir).invariantSeparatorsPath
                rel.isEmpty() || filter(rel)
            }
            .filter { f ->
                val rel = f.relativeTo(baseDir).invariantSeparatorsPath
                rel.isNotEmpty() && filter(rel)
            }
            .sortedBy { it.relativeTo(baseDir).invariantSeparatorsPath }
            .toList()

        val buf = ByteArray(65536)
        for (f in files) {
            val rel = f.relativeTo(baseDir).invariantSeparatorsPath
            val arcName = if (prefix.isEmpty()) rel else "$prefix/$rel"
            if (f.isDirectory) {
                writeTarHeader(out, "$arcName/", 0L, isDir = true, executable = true)
            } else if (f.isFile) {
                val len = f.length()
                writeTarHeader(out, arcName, len, isDir = false, executable = f.canExecute())
                FileInputStream(f).use { fis ->
                    var left = len
                    while (left > 0) {
                        val n = fis.read(buf, 0, minOf(buf.size.toLong(), left).toInt())
                        if (n <= 0) break
                        out.write(buf, 0, n)
                        left -= n
                    }
                }
                val rem = (512 - (len % 512)) % 512
                if (rem > 0) {
                    out.write(ByteArray(rem.toInt()))
                }
            }
        }
    }

    @TaskAction
    fun run() {
        val outBase = outputDir.get().asFile
        outBase.mkdirs()
        outBase.listFiles { f -> f.isFile && f.name.startsWith("kit-") && f.name.endsWith(".tgz") }
            ?.forEach { it.delete() }

        for (abi in abis.get()) {
            val abiKit = File(stageDir.get().asFile, "$abi/kit/$abi")
            if (!abiKit.isDirectory) {
                logger.warn("Skipping kit packaging for missing ABI $abi")
                continue
            }
            val outFile = File(outBase, "kit-$abi.tgz")
            GZIPOutputStream(BufferedOutputStream(FileOutputStream(outFile))).use { gz ->
                BufferedOutputStream(gz, 65536).use { tar ->
                    addTree(tar, abiKit, "") { true }
                    tar.write(ByteArray(1024))
                }
            }
            logger.lifecycle("Packaged ${outFile.name} (${outFile.length() / 1024} KiB)")
        }
    }
}

abstract class BuildGameModuleManifestTask : DefaultTask() {
    @get:Input
    abstract val sdkDirPath: Property<String>

    @get:Input
    abstract val compileSdkVersion: Property<Int>

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun run() {
        val sdk = File(sdkDirPath.get())
        val buildToolsBase = File(sdk, "build-tools")
        val aapt2 = buildToolsBase.listFiles()
            ?.filter { it.isDirectory }
            ?.sortedByDescending { it.name }
            ?.map { File(it, "aapt2") }
            ?.firstOrNull { it.canExecute() }
            ?: error("aapt2 not found under ${buildToolsBase.absolutePath}")

        val platformsBase = File(sdk, "platforms")
        val androidJar = listOf(
            File(platformsBase, "android-${compileSdkVersion.get()}/android.jar"),
            File(platformsBase, "android-${compileSdkVersion.get()}.0/android.jar")
        ).firstOrNull { it.isFile }
            ?: platformsBase.listFiles()
                ?.filter { it.isDirectory }
                ?.sortedByDescending { it.name }
                ?.map { File(it, "android.jar") }
                ?.firstOrNull { it.isFile }
            ?: error("android.jar not found under ${platformsBase.absolutePath}")

        val tmpDir = temporaryDir
        tmpDir.mkdirs()
        val manifestXml = File(tmpDir, "AndroidManifest.xml")
        manifestXml.writeText(
            """
            <?xml version="1.0" encoding="utf-8"?>
            <manifest xmlns:android="http://schemas.android.com/apk/res/android"
                package="io.emotionalsense.p3.gamemodule"
                android:versionCode="1"
                android:versionName="1.0">
                <uses-sdk android:minSdkVersion="26" android:targetSdkVersion="36" />
                <application
                    android:hasCode="false"
                    android:extractNativeLibs="true"
                    android:label="EmotionalSense-P3 Game Module" />
            </manifest>
            """.trimIndent() + "\n"
        )
        val tmpApk = File(tmpDir, "module_manifest.apk")
        val pb = ProcessBuilder(
            aapt2.absolutePath, "link",
            "--manifest", manifestXml.absolutePath,
            "-I", androidJar.absolutePath,
            "-o", tmpApk.absolutePath
        )
        pb.redirectErrorStream(true)
        val proc = pb.start()
        val out = proc.inputStream.bufferedReader().readText()
        val rc = proc.waitFor()
        if (rc != 0) {
            error("aapt2 link failed ($rc): $out")
        }

        val outBase = outputDir.get().asFile
        outBase.mkdirs()
        val outBin = File(outBase, "gamemodule_manifest.bin")
        ZipFile(tmpApk).use { zip ->
            val entry = zip.getEntry("AndroidManifest.xml")
                ?: error("AndroidManifest.xml missing in aapt2 output")
            zip.getInputStream(entry).use { input ->
                FileOutputStream(outBin).use { fos -> input.copyTo(fos) }
            }
        }
        logger.lifecycle("Generated ${outBin.name} (${outBin.length()} bytes)")
    }
}

val packageAndroidKit = tasks.register<PackageKitTask>("packageAndroidKit") {
    stageDir.set(stageRoot)
    abis.set(supportedAbis)
    outputDir.set(generatedKitAssetsDir)
}

val buildGameModuleManifest = tasks.register<BuildGameModuleManifestTask>("buildGameModuleManifest") {
    sdkDirPath.set(
        androidComponents.sdkComponents.sdkDirectory.map { it.asFile.absolutePath }
    )
    compileSdkVersion.set(37)
    outputDir.set(generatedModuleAssetsDir)
}

tasks.named("preBuild") {
    dependsOn(packageAndroidKit, buildGameModuleManifest)
}
