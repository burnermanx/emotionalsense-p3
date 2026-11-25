package io.emotionalsense.p3

import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest

enum class ReadinessState {
    NEEDS_ISO,
    NEEDS_PREPARE_FIRST,
    NEEDS_REPREPARE_LAUNCHER_UPDATED,
    NEEDS_INSTALL,
    READY
}

object ModuleState {
    const val MODULE_SCHEMA_VERSION = 1
    private const val STATE_FILE = "module_state.json"
    private const val SO_NAME = "libemotionalsense_game.so"

    data class PersistedState(
        val schemaVersion: Int = MODULE_SCHEMA_VERSION,
        val preparedKitStamp: String = "",
        val preparedSoSha256: String = "",
        val preparedVersionCode: Long = 0L,
        val installedKitStamp: String = "",
        val installedSoSha256: String = "",
        val installedVersionCode: Long = 0L
    )

    data class SystemModuleInfo(
        val versionCode: Long,
        val soFile: File,
        val soSha256: String
    )

    data class StatusSnapshot(
        val readiness: ReadinessState,
        val currentKitStamp: String,
        val shortStamp: String,
        val discReady: Boolean,
        val preparedSoExists: Boolean,
        val preparedKitStamp: String,
        val preparedSoSha256: String,
        val targetVersionCode: Long,
        val installedPackagePresent: Boolean,
        val installedSoExists: Boolean,
        val installedVersionCode: Long,
        val installedSoSha256: String
    )

    private data class CachedSoHash(
        val path: String,
        val length: Long,
        val lastModified: Long,
        val sha256: String
    )

    @Volatile
    private var cachedInstalledHash: CachedSoHash? = null

    @Volatile
    private var cachedPreparedHash: CachedSoHash? = null

    val currentKitStamp: String
        get() = BuildConfig.KIT_VERSION_STAMP

    fun shortStamp(stamp: String = currentKitStamp): String {
        val tail = stamp.substringAfterLast('-', stamp)
        return if (tail.length > 8) tail.take(8) else tail
    }

    fun stateFile(context: Context): File = File(context.filesDir, STATE_FILE)

    fun load(context: Context): PersistedState {
        val f = stateFile(context)
        if (!f.isFile) return PersistedState()
        return try {
            val j = JSONObject(f.readText())
            PersistedState(
                schemaVersion = j.optInt("schemaVersion", MODULE_SCHEMA_VERSION),
                preparedKitStamp = j.optString("preparedKitStamp", ""),
                preparedSoSha256 = j.optString("preparedSoSha256", ""),
                preparedVersionCode = j.optLong("preparedVersionCode", 0L),
                installedKitStamp = j.optString("installedKitStamp", ""),
                installedSoSha256 = j.optString("installedSoSha256", ""),
                installedVersionCode = j.optLong("installedVersionCode", 0L)
            )
        } catch (_: Throwable) {
            PersistedState()
        }
    }

    fun save(context: Context, state: PersistedState) {
        val j = JSONObject()
            .put("schemaVersion", state.schemaVersion)
            .put("preparedKitStamp", state.preparedKitStamp)
            .put("preparedSoSha256", state.preparedSoSha256)
            .put("preparedVersionCode", state.preparedVersionCode)
            .put("installedKitStamp", state.installedKitStamp)
            .put("installedSoSha256", state.installedSoSha256)
            .put("installedVersionCode", state.installedVersionCode)
        stateFile(context).writeText(j.toString(2))
    }

    fun sha256File(file: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(65536)
        FileInputStream(file).use { fis ->
            while (true) {
                val n = fis.read(buf)
                if (n <= 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    private fun sha256FileCached(file: File, isInstalled: Boolean): String {
        val len = file.length()
        val mtime = file.lastModified()
        val path = file.absolutePath
        val cached = if (isInstalled) cachedInstalledHash else cachedPreparedHash
        if (cached != null && cached.path == path && cached.length == len && cached.lastModified == mtime) {
            return cached.sha256
        }
        val hash = sha256File(file)
        val entry = CachedSoHash(path, len, mtime, hash)
        if (isInstalled) {
            cachedInstalledHash = entry
        } else {
            cachedPreparedHash = entry
        }
        return hash
    }

    private fun getPackageInfo(context: Context): PackageInfo? {
        return try {
            if (Build.VERSION.SDK_INT >= 33) {
                context.packageManager.getPackageInfo(
                    GameModulePackager.MODULE_PACKAGE,
                    PackageManager.PackageInfoFlags.of(0)
                )
            } else {
                @Suppress("DEPRECATION")
                context.packageManager.getPackageInfo(GameModulePackager.MODULE_PACKAGE, 0)
            }
        } catch (_: PackageManager.NameNotFoundException) {
            null
        }
    }

    private fun packageVersionCode(pkg: PackageInfo): Long {
        return if (Build.VERSION.SDK_INT >= 28) {
            pkg.longVersionCode
        } else {
            @Suppress("DEPRECATION")
            pkg.versionCode.toLong()
        }
    }

    fun getSystemModuleInfo(context: Context): Pair<Boolean, SystemModuleInfo?> {
        val pkg = getPackageInfo(context) ?: return false to null
        val appInfo = pkg.applicationInfo ?: return true to null
        val soFile = File(appInfo.nativeLibraryDir, SO_NAME)
        if (!soFile.isFile) return true to null
        val sha = try {
            sha256FileCached(soFile, isInstalled = true)
        } catch (_: Throwable) {
            ""
        }
        return true to SystemModuleInfo(
            versionCode = packageVersionCode(pkg),
            soFile = soFile,
            soSha256 = sha
        )
    }

    fun recordPrepared(context: Context, soFile: File, soSha256: String): PersistedState {
        val prev = load(context)
        val (_, sysMod) = getSystemModuleInfo(context)
        val sysVc = sysMod?.versionCode ?: 0L
        val nextVc = maxOf(
            prev.preparedVersionCode + 1L,
            prev.installedVersionCode + 1L,
            sysVc + 1L,
            1L
        )
        cachedPreparedHash = CachedSoHash(
            path = soFile.absolutePath,
            length = soFile.length(),
            lastModified = soFile.lastModified(),
            sha256 = soSha256
        )
        val updated = prev.copy(
            schemaVersion = MODULE_SCHEMA_VERSION,
            preparedKitStamp = currentKitStamp,
            preparedSoSha256 = soSha256,
            preparedVersionCode = nextVc
        )
        save(context, updated)
        return updated
    }

    fun nextInstallVersionCode(context: Context): Long {
        val state = load(context)
        val (_, sysMod) = getSystemModuleInfo(context)
        val sysVc = sysMod?.versionCode ?: 0L
        val builtSo = RecompPipeline.getBuiltGameSo(context)
        val preparedSha = if (state.preparedSoSha256.isNotEmpty()) {
            state.preparedSoSha256
        } else if (builtSo.isFile) {
            sha256FileCached(builtSo, isInstalled = false)
        } else {
            ""
        }
        val minUpgrade = if (sysMod != null && sysMod.soSha256 != preparedSha) sysVc + 1L else sysVc
        return maxOf(state.preparedVersionCode, minUpgrade, 1L)
    }

    fun recordInstalled(context: Context, fallbackVersionCode: Long = 0L): PersistedState {
        val prev = load(context)
        cachedInstalledHash = null
        val (_, sysMod) = getSystemModuleInfo(context)
        val builtSo = RecompPipeline.getBuiltGameSo(context)
        val builtSha = if (builtSo.isFile) sha256FileCached(builtSo, isInstalled = false) else prev.preparedSoSha256
        val installedSha = sysMod?.soSha256?.ifEmpty { builtSha } ?: builtSha
        val installedVc = sysMod?.versionCode ?: maxOf(fallbackVersionCode, prev.preparedVersionCode, 1L)
        val kitStamp = if (prev.preparedKitStamp.isNotEmpty()) prev.preparedKitStamp else currentKitStamp
        val updated = prev.copy(
            schemaVersion = MODULE_SCHEMA_VERSION,
            installedKitStamp = kitStamp,
            installedSoSha256 = installedSha,
            installedVersionCode = installedVc
        )
        save(context, updated)
        return updated
    }

    fun evaluate(context: Context): StatusSnapshot {
        val state = load(context)
        val stamp = currentKitStamp
        val discReady = IsoExtractor.isDiscReady(context.filesDir)
        val builtSo = RecompPipeline.getBuiltGameSo(context)
        val builtExists = builtSo.isFile
        val (pkgPresent, sysMod) = getSystemModuleInfo(context)

        val preparedSha = when {
            !builtExists -> ""
            state.preparedSoSha256.isNotEmpty() -> state.preparedSoSha256
            else -> sha256FileCached(builtSo, isInstalled = false)
        }

        val targetVc = maxOf(
            state.preparedVersionCode,
            if (sysMod != null && builtExists && sysMod.soSha256 != preparedSha) sysMod.versionCode + 1L else (sysMod?.versionCode ?: 0L),
            1L
        )

        val readiness = when {
            !discReady -> ReadinessState.NEEDS_ISO
            !builtExists && !pkgPresent -> ReadinessState.NEEDS_PREPARE_FIRST
            !builtExists && pkgPresent -> ReadinessState.NEEDS_REPREPARE_LAUNCHER_UPDATED
            state.preparedKitStamp != stamp -> ReadinessState.NEEDS_REPREPARE_LAUNCHER_UPDATED
            sysMod == null || sysMod.soSha256.isEmpty() || sysMod.soSha256 != preparedSha ->
                ReadinessState.NEEDS_INSTALL
            else -> ReadinessState.READY
        }

        return StatusSnapshot(
            readiness = readiness,
            currentKitStamp = stamp,
            shortStamp = shortStamp(stamp),
            discReady = discReady,
            preparedSoExists = builtExists,
            preparedKitStamp = state.preparedKitStamp,
            preparedSoSha256 = preparedSha,
            targetVersionCode = targetVc,
            installedPackagePresent = pkgPresent,
            installedSoExists = sysMod != null,
            installedVersionCode = sysMod?.versionCode ?: state.installedVersionCode,
            installedSoSha256 = sysMod?.soSha256 ?: state.installedSoSha256
        )
    }
}
