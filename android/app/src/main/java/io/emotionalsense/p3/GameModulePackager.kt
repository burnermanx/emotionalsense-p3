package io.emotionalsense.p3

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import com.android.apksig.ApkSigner
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.FilterOutputStream
import java.io.IOException
import java.io.OutputStream
import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.Signature
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

object GameModulePackager {
    const val MODULE_PACKAGE = BuildConfig.GAME_MODULE_PACKAGE
    const val ACTION_INSTALL_RESULT = "io.emotionalsense.p3.ACTION_GAME_MODULE_INSTALL"
    private const val PAGE_ALIGN = 16384

    private class CountingOutputStream(out: OutputStream) : FilterOutputStream(out) {
        var count: Long = 0L
            private set

        override fun write(b: Int) {
            out.write(b)
            count++
        }

        override fun write(b: ByteArray, off: Int, len: Int) {
            out.write(b, off, len)
            count += len
        }
    }

    fun getInstalledModuleSo(context: Context): File? {
        return ModuleState.getSystemModuleInfo(context).second?.soFile
    }

    fun isModuleInstalledAndCurrent(context: Context): Boolean {
        return ModuleState.evaluate(context).readiness == ReadinessState.READY
    }

    fun markModuleInstalled(context: Context) {
        ModuleState.recordInstalled(context)
    }

    private fun patchManifestVersionCode(raw: ByteArray, versionCode: Int): ByteArray {
        val out = raw.copyOf()
        val bb = ByteBuffer.wrap(out).order(ByteOrder.LITTLE_ENDIAN)
        if (out.size < 8) return out
        var pos = 8
        var resIds = IntArray(0)
        while (pos + 8 <= out.size) {
            val ctype = bb.getShort(pos).toInt() and 0xFFFF
            val hdrSize = bb.getShort(pos + 2).toInt() and 0xFFFF
            val chunkSize = bb.getInt(pos + 4)
            if (chunkSize < 8 || pos + chunkSize > out.size) break
            if (ctype == 0x0180) {
                val count = (chunkSize - hdrSize) / 4
                resIds = IntArray(count) { idx -> bb.getInt(pos + hdrSize + idx * 4) }
            } else if (ctype == 0x0102 && pos + hdrSize + 14 <= out.size) {
                val ext = pos + hdrSize
                val attrStart = bb.getShort(ext + 8).toInt() and 0xFFFF
                val attrSize = bb.getShort(ext + 10).toInt() and 0xFFFF
                val attrCount = bb.getShort(ext + 12).toInt() and 0xFFFF
                for (i in 0 until attrCount) {
                    val ao = ext + attrStart + i * attrSize
                    if (ao + 20 > pos + chunkSize) break
                    val nameIdx = bb.getInt(ao + 4)
                    val vtype = out[ao + 15].toInt() and 0xFF
                    if (nameIdx in resIds.indices && resIds[nameIdx] == 0x0101021b && vtype == 0x10) {
                        bb.putInt(ao + 16, versionCode)
                        return out
                    }
                }
            }
            pos += chunkSize
        }
        return out
    }

    fun buildAndSignModuleApk(context: Context, soFile: File, log: (String) -> Unit): File {
        if (!soFile.isFile) throw IOException("Built game library missing: ${soFile.absolutePath}")
        val abi = RecompPipeline.detectAbi(context).abi

        val buildDir = File(context.filesDir, "build").apply { mkdirs() }
        val unsignedApk = File(buildDir, "gamemodule-unsigned.apk")
        val signedApk = File(buildDir, "gamemodule-signed.apk")

        val versionCode = ModuleState.nextInstallVersionCode(context).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
        val rawManifestBytes = context.assets.open("gamemodule_manifest.bin").use { it.readBytes() }
        val manifestBytes = patchManifestVersionCode(rawManifestBytes, versionCode)
        val soEntryName = "lib/$abi/libemotionalsense_game.so"

        log("Packaging $soEntryName (v$versionCode, ${soFile.length() / (1024 * 1024)} MiB) into mini-APK...")
        val crc = CRC32()
        val buf = ByteArray(65536)
        FileInputStream(soFile).use { fis ->
            while (true) {
                val n = fis.read(buf)
                if (n <= 0) break
                crc.update(buf, 0, n)
            }
        }
        val soSize = soFile.length()
        val soCrc = crc.value

        val manifestCrc = CRC32().apply { update(manifestBytes) }.value

        CountingOutputStream(FileOutputStream(unsignedApk)).use { cos ->
            ZipOutputStream(cos).use { zos ->
                // 1. AndroidManifest.xml (STORED)
                val mfEntry = ZipEntry("AndroidManifest.xml").apply {
                    method = ZipEntry.STORED
                    size = manifestBytes.size.toLong()
                    compressedSize = manifestBytes.size.toLong()
                    this.crc = manifestCrc
                }
                zos.putNextEntry(mfEntry)
                zos.write(manifestBytes)
                zos.closeEntry()
                zos.flush()

                // 2. lib/<abi>/libemotionalsense_game.so (STORED, 16 KiB aligned)
                val nameBytes = soEntryName.toByteArray(Charsets.UTF_8)
                val headerStart = cos.count
                val unpaddedDataStart = headerStart + 30L + nameBytes.size
                val pad = ((PAGE_ALIGN - (unpaddedDataStart % PAGE_ALIGN)) % PAGE_ALIGN).toInt()
                val soEntry = ZipEntry(soEntryName).apply {
                    method = ZipEntry.STORED
                    size = soSize
                    compressedSize = soSize
                    this.crc = soCrc
                    if (pad > 0) {
                        extra = ByteArray(pad)
                    }
                }
                zos.putNextEntry(soEntry)
                FileInputStream(soFile).use { fis ->
                    while (true) {
                        val n = fis.read(buf)
                        if (n <= 0) break
                        zos.write(buf, 0, n)
                    }
                }
                zos.closeEntry()
            }
        }

        val (privKey, cert) = loadOrCreateSigningIdentity(context)
        log("Signing mini-APK with APK Signature Scheme v2...")
        val signerConfig = ApkSigner.SignerConfig.Builder(
            "EmotionalSenseLocal",
            privKey,
            listOf(cert)
        ).build()

        if (signedApk.exists()) signedApk.delete()
        ApkSigner.Builder(listOf(signerConfig))
            .setInputApk(unsignedApk)
            .setOutputApk(signedApk)
            .setV1SigningEnabled(false)
            .setV2SigningEnabled(true)
            .setV3SigningEnabled(false)
            .setMinSdkVersion(26)
            .build()
            .sign()

        unsignedApk.delete()
        log("Signed mini-APK: ${signedApk.name} (${signedApk.length() / (1024 * 1024)} MiB)")
        return signedApk
    }

    fun installModuleApk(context: Context, signedApk: File, log: (String) -> Unit) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(MODULE_PACKAGE)
            setSize(signedApk.length())
        }
        val sessionId = installer.createSession(params)
        val session = installer.openSession(sessionId)
        try {
            session.openWrite("gamemodule.apk", 0, signedApk.length()).use { out ->
                FileInputStream(signedApk).use { input ->
                    input.copyTo(out, 65536)
                }
                session.fsync(out)
            }
            val intent = Intent(ACTION_INSTALL_RESULT).setPackage(context.packageName)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
            val pending = PendingIntent.getBroadcast(context, sessionId, intent, flags)
            log("Committing PackageInstaller session #$sessionId...")
            session.commit(pending.intentSender)
        } catch (t: Throwable) {
            session.abandon()
            throw t
        }
    }

    private fun loadOrCreateSigningIdentity(context: Context): Pair<PrivateKey, X509Certificate> {
        val dir = File(context.filesDir, "signing").apply { mkdirs() }
        val keyFile = File(dir, "module_key.pk8")
        val certFile = File(dir, "module_cert.der")

        if (keyFile.isFile && certFile.isFile) {
            val kf = KeyFactory.getInstance("RSA")
            val priv = kf.generatePrivate(PKCS8EncodedKeySpec(keyFile.readBytes()))
            val cf = CertificateFactory.getInstance("X.509")
            val cert = cf.generateCertificate(ByteArrayInputStream(certFile.readBytes())) as X509Certificate
            return priv to cert
        }

        val kpg = KeyPairGenerator.getInstance("RSA")
        kpg.initialize(2048, SecureRandom())
        val kp = kpg.generateKeyPair()
        val certDer = createSelfSignedCertDer(kp.public.encoded, kp.private)
        val cf = CertificateFactory.getInstance("X.509")
        val cert = cf.generateCertificate(ByteArrayInputStream(certDer)) as X509Certificate

        keyFile.writeBytes(kp.private.encoded)
        certFile.writeBytes(certDer)
        return kp.private to cert
    }

    /**
     * Minimal pure-Java DER builder for a self-signed X.509 v3 SHA256withRSA certificate.
     */
    private fun createSelfSignedCertDer(spkiEncoded: ByteArray, privateKey: PrivateKey): ByteArray {
        val sha256WithRsaAlgId = derSeq(
            derOid(intArrayOf(1, 2, 840, 113549, 1, 1, 11)), // sha256WithRSAEncryption
            byteArrayOf(0x05, 0x00) // NULL
        )
        val cnAttr = derSet(
            derSeq(
                derOid(intArrayOf(2, 5, 4, 3)), // commonName
                derUtf8("EmotionalSense-P3 Local")
            )
        )
        val name = derSeq(cnAttr)
        val validity = derSeq(
            derUtcTime("240101000000Z"),
            derUtcTime("491231235959Z")
        )
        val serial = BigInteger(63, SecureRandom()).abs().add(BigInteger.ONE)

        val tbs = derSeq(
            derTag(0xA0, derInteger(BigInteger.valueOf(2))), // v3
            derInteger(serial),
            sha256WithRsaAlgId,
            name,
            validity,
            name,
            spkiEncoded
        )

        val sig = Signature.getInstance("SHA256withRSA").apply {
            initSign(privateKey)
            update(tbs)
        }.sign()

        val sigBitString = ByteArrayOutputStream().apply {
            write(0x03)
            writeDerLen(sig.size + 1)
            write(0x00)
            write(sig)
        }.toByteArray()

        return derSeq(tbs, sha256WithRsaAlgId, sigBitString)
    }

    private fun derTag(tag: Int, content: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        out.write(tag)
        out.writeDerLen(content.size)
        out.write(content)
        return out.toByteArray()
    }

    private fun derSeq(vararg items: ByteArray): ByteArray {
        val body = ByteArrayOutputStream()
        for (item in items) body.write(item)
        return derTag(0x30, body.toByteArray())
    }

    private fun derSet(vararg items: ByteArray): ByteArray {
        val body = ByteArrayOutputStream()
        for (item in items) body.write(item)
        return derTag(0x31, body.toByteArray())
    }

    private fun derInteger(v: BigInteger): ByteArray = derTag(0x02, v.toByteArray())

    private fun derUtf8(s: String): ByteArray = derTag(0x0C, s.toByteArray(Charsets.UTF_8))

    private fun derUtcTime(s: String): ByteArray = derTag(0x17, s.toByteArray(Charsets.US_ASCII))

    private fun derOid(parts: IntArray): ByteArray {
        val body = ByteArrayOutputStream()
        body.write(parts[0] * 40 + parts[1])
        for (i in 2 until parts.size) {
            var v = parts[i]
            val stack = IntArray(5)
            var sp = 0
            stack[sp++] = v and 0x7F
            v = v ushr 7
            while (v > 0) {
                stack[sp++] = (v and 0x7F) or 0x80
                v = v ushr 7
            }
            for (j in sp - 1 downTo 0) body.write(stack[j])
        }
        return derTag(0x06, body.toByteArray())
    }

    private fun ByteArrayOutputStream.writeDerLen(len: Int) {
        if (len < 128) {
            write(len)
        } else {
            val bytes = ByteArrayOutputStream()
            var v = len
            while (v > 0) {
                bytes.write(v and 0xFF)
                v = v ushr 8
            }
            val arr = bytes.toByteArray().reversedArray()
            write(0x80 or arr.size)
            write(arr)
        }
    }
}
