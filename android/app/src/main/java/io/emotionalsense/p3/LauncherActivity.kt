package io.emotionalsense.p3

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageInstaller
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

class LauncherActivity : Activity() {

    companion object {
        private const val REQ_PICK_ISO = 1001
        private const val REQ_INSTALL_PERM = 1002
        private const val PREFS_NAME = "es_prefs"
        private val TOUCH_MODE_VALUES = listOf("auto", "on", "off")
    }

    private lateinit var tvStatus: TextView
    private lateinit var tvIso: TextView
    private lateinit var btnPickIso: Button
    private lateinit var btnPrepare: Button
    private lateinit var btnInstallModule: Button
    private lateinit var btnPlay: Button
    private lateinit var spRenderer: Spinner
    private lateinit var spTouchMode: Spinner
    private lateinit var etLatency: EditText
    private lateinit var cbVerbose: CheckBox
    private lateinit var progressBar: ProgressBar
    private lateinit var tvProgress: TextView
    private lateinit var svLog: ScrollView
    private lateinit var tvLog: TextView

    private var selectedIso: IsoExtractor.IsoSourceSpec? = null
    private val isBusy = AtomicBoolean(false)
    private val cancelRequested = AtomicBoolean(false)
    private var workerThread: Thread? = null
    private var pendingInstallAfterPerm = false

    private val installReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
            val msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: ""
            when (status) {
                PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                    @Suppress("DEPRECATION")
                    val confirmIntent = if (Build.VERSION.SDK_INT >= 33) {
                        intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                    } else {
                        intent.getParcelableExtra(Intent.EXTRA_INTENT)
                    }
                    if (confirmIntent != null) {
                        confirmIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        startActivity(confirmIntent)
                    }
                }
                PackageInstaller.STATUS_SUCCESS -> {
                    GameModulePackager.markModuleInstalled(this@LauncherActivity)
                    appendLog("Game module installed successfully!")
                    isBusy.set(false)
                    refreshUiState()
                }
                else -> {
                    appendLog("Module install failed (status=$status): $msg")
                    isBusy.set(false)
                    refreshUiState()
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildUi()

        val filter = IntentFilter(GameModulePackager.ACTION_INSTALL_RESULT)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(installReceiver, filter, RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            registerReceiver(installReceiver, filter)
        }

        detectLocalIso()
        initKitInBackground()
        refreshUiState()
    }

    override fun onResume() {
        super.onResume()
        if (pendingInstallAfterPerm && Build.VERSION.SDK_INT >= 26 && packageManager.canRequestPackageInstalls()) {
            pendingInstallAfterPerm = false
            startInstallModule()
        } else {
            refreshUiState()
        }
    }

    override fun onDestroy() {
        unregisterReceiver(installReceiver)
        super.onDestroy()
    }

    private fun buildUi() {
        val pad = (16 * resources.displayMetrics.density).toInt()
        val gap = (10 * resources.displayMetrics.density).toInt()

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
            setBackgroundColor(Color.parseColor("#121417"))
        }

        val title = TextView(this).apply {
            text = "EmotionalSense-P3 (Android)"
            textSize = 20f
            setTypeface(null, Typeface.BOLD)
            setTextColor(Color.WHITE)
        }
        root.addView(title)

        tvStatus = TextView(this).apply {
            textSize = 13f
            setTextColor(Color.parseColor("#A8B3C2"))
            setPadding(0, gap / 2, 0, gap)
        }
        root.addView(tvStatus)

        tvIso = TextView(this).apply {
            text = "ISO: none selected"
            textSize = 14f
            setTextColor(Color.parseColor("#E6EDF3"))
            setPadding(0, 0, 0, gap / 2)
        }
        root.addView(tvIso)

        val row1 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        btnPickIso = Button(this).apply {
            text = "Selecionar ISO"
            setOnClickListener { pickIso() }
        }
        btnPrepare = Button(this).apply {
            text = "Preparar"
            setOnClickListener {
                if (isBusy.get()) {
                    cancelRequested.set(true)
                    appendLog("Cancellation requested...")
                } else {
                    startPrepare()
                }
            }
        }
        row1.addView(btnPickIso, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        row1.addView(btnPrepare, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(row1)

        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)

        val rowOpts = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, gap / 2, 0, gap / 4)
        }
        val lblRenderer = TextView(this).apply {
            text = "Renderer: "
            setTextColor(Color.parseColor("#E6EDF3"))
        }
        spRenderer = Spinner(this).apply {
            adapter = ArrayAdapter(
                this@LauncherActivity,
                android.R.layout.simple_spinner_dropdown_item,
                listOf("vulkan", "software")
            )
            val saved = prefs.getString("renderer", "vulkan")
            setSelection(if (saved == "software") 1 else 0)
        }
        val lblLat = TextView(this).apply {
            text = "  Áudio (ms): "
            setTextColor(Color.parseColor("#E6EDF3"))
        }
        etLatency = EditText(this).apply {
            setText(prefs.getInt("audio_latency_ms", 70).toString())
            inputType = InputType.TYPE_CLASS_NUMBER
            setTextColor(Color.WHITE)
            setEms(3)
        }
        cbVerbose = CheckBox(this).apply {
            text = "Log"
            setTextColor(Color.parseColor("#E6EDF3"))
            isChecked = prefs.getBoolean("verbose", false)
        }
        rowOpts.addView(lblRenderer)
        rowOpts.addView(spRenderer)
        rowOpts.addView(lblLat)
        rowOpts.addView(etLatency)
        rowOpts.addView(cbVerbose)
        root.addView(rowOpts)

        val rowTouch = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, 0, 0, gap / 2)
        }
        val lblTouch = TextView(this).apply {
            text = "Controles na tela: "
            setTextColor(Color.parseColor("#E6EDF3"))
        }
        spTouchMode = Spinner(this).apply {
            adapter = ArrayAdapter(
                this@LauncherActivity,
                android.R.layout.simple_spinner_dropdown_item,
                listOf("Auto", "Sempre", "Desligado")
            )
            val savedTouch = prefs.getString("touch_mode", "auto")
            val idx = TOUCH_MODE_VALUES.indexOf(savedTouch).let { if (it >= 0) it else 0 }
            setSelection(idx)
        }
        rowTouch.addView(lblTouch)
        rowTouch.addView(spTouchMode)
        root.addView(rowTouch)

        val row2 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        btnInstallModule = Button(this).apply {
            text = "Instalar Módulo"
            setOnClickListener { startInstallModule() }
        }
        btnPlay = Button(this).apply {
            text = "Jogar"
            setOnClickListener { launchGame() }
        }
        row2.addView(btnInstallModule, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        row2.addView(btnPlay, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(row2)

        progressBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 1000
            progress = 0
            setPadding(0, gap, 0, 0)
        }
        root.addView(progressBar)

        tvProgress = TextView(this).apply {
            text = "Idle"
            textSize = 12f
            setTextColor(Color.parseColor("#8B949E"))
            setPadding(0, 4, 0, gap / 2)
        }
        root.addView(tvProgress)

        tvLog = TextView(this).apply {
            typeface = Typeface.MONOSPACE
            textSize = 11f
            setTextColor(Color.parseColor("#C9D1D9"))
        }
        svLog = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#0D1117"))
            setPadding(pad / 2, pad / 2, pad / 2, pad / 2)
            addView(tvLog)
        }
        root.addView(svLog, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            1f
        ))

        setContentView(root)
    }

    private fun detectLocalIso() {
        val extDir = getExternalFilesDir(null) ?: return
        val found = extDir.listFiles { f ->
            f.isFile && (
                f.name.endsWith(".iso", ignoreCase = true) ||
                    f.name.endsWith(".cso", ignoreCase = true) ||
                    f.name.endsWith(".zso", ignoreCase = true)
                )
        }?.sortedBy { it.name }?.firstOrNull()

        if (found != null) {
            selectedIso = IsoExtractor.IsoSourceSpec.LocalFile(found)
            tvIso.text = "ISO (auto): ${found.name} (${found.length() / (1024 * 1024)} MiB)"
            appendLog("Detected local disc image: ${found.absolutePath}")
        } else if (IsoExtractor.isDiscReady(filesDir)) {
            tvIso.text = "ISO: cached disc ready in filesDir/disc"
        } else {
            tvIso.text = "ISO: none (place in ${extDir.absolutePath} or pick via SAF)"
        }
    }

    private fun initKitInBackground() {
        Thread {
            try {
                RecompPipeline.ensureKitExtracted(this, log = { appendLog(it) })
                runOnUiThread { refreshUiState() }
            } catch (t: Throwable) {
                appendLog("Kit init error: ${t.message}")
            }
        }.start()
    }

    private fun pickIso() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
        }
        @Suppress("DEPRECATION")
        startActivityForResult(intent, REQ_PICK_ISO)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_PICK_ISO && resultCode == RESULT_OK) {
            val uri = data?.data ?: return
            try {
                contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            } catch (_: SecurityException) {
            }
            val name = queryDisplayName(uri) ?: uri.lastPathSegment ?: "disc.iso"
            selectedIso = IsoExtractor.IsoSourceSpec.ContentUri(uri, name)
            tvIso.text = "ISO (SAF): $name"
            appendLog("Selected SAF URI: $name ($uri)")
            refreshUiState()
        }
    }

    private fun queryDisplayName(uri: Uri): String? {
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0) return c.getString(idx)
            }
        }
        return null
    }

    private fun startPrepare() {
        if (selectedIso == null && !IsoExtractor.isDiscReady(filesDir)) {
            appendLog("Please select an ISO/CSO/ZSO first.")
            return
        }
        if (!isBusy.compareAndSet(false, true)) return
        cancelRequested.set(false)
        refreshUiState()

        workerThread = Thread {
            try {
                RecompPipeline.runFullPipeline(
                    context = this,
                    isoSpec = selectedIso,
                    force = false,
                    log = { appendLog(it) },
                    progress = { frac, label ->
                        runOnUiThread {
                            progressBar.progress = (frac.coerceIn(0f, 1f) * 1000).toInt()
                            tvProgress.text = label
                        }
                    },
                    cancelled = { cancelRequested.get() }
                )
            } catch (t: Throwable) {
                appendLog("ERROR: ${t.message}")
                t.printStackTrace()
            } finally {
                isBusy.set(false)
                runOnUiThread { refreshUiState() }
            }
        }.also { it.start() }
    }

    private fun startInstallModule() {
        val soFile = RecompPipeline.getBuiltGameSo(this)
        if (!soFile.isFile) {
            appendLog("Run Preparar first so libemotionalsense_game.so is built.")
            return
        }
        if (Build.VERSION.SDK_INT >= 26 && !packageManager.canRequestPackageInstalls()) {
            pendingInstallAfterPerm = true
            appendLog("Grant 'Install unknown apps' permission for EmotionalSense-P3...")
            val permIntent = Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:$packageName")
            )
            @Suppress("DEPRECATION")
            startActivityForResult(permIntent, REQ_INSTALL_PERM)
            return
        }
        if (!isBusy.compareAndSet(false, true)) return
        refreshUiState()

        Thread {
            try {
                val signedApk = GameModulePackager.buildAndSignModuleApk(this, soFile) { appendLog(it) }
                GameModulePackager.installModuleApk(this, signedApk) { appendLog(it) }
            } catch (t: Throwable) {
                appendLog("Install error: ${t.message}")
                t.printStackTrace()
                isBusy.set(false)
                runOnUiThread { refreshUiState() }
            }
        }.start()
    }

    private fun launchGame() {
        if (GameModulePackager.getInstalledModuleSo(this) == null) {
            appendLog("Game module is not installed yet.")
            return
        }
        val renderer = spRenderer.selectedItem?.toString() ?: "vulkan"
        val latency = etLatency.text.toString().toIntOrNull()?.coerceIn(10, 500) ?: 70
        val touchMode = TOUCH_MODE_VALUES.getOrElse(spTouchMode.selectedItemPosition) { "auto" }
        val verbose = cbVerbose.isChecked
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit()
            .putString("renderer", renderer)
            .putInt("audio_latency_ms", latency)
            .putString("touch_mode", touchMode)
            .putBoolean("verbose", verbose)
            .apply()
        val intent = Intent(this, GameActivity::class.java).apply {
            putExtra(GameActivity.EXTRA_RENDERER, renderer)
            putExtra(GameActivity.EXTRA_AUDIO_LATENCY_MS, latency)
            putExtra(GameActivity.EXTRA_TOUCH_MODE, touchMode)
            putExtra(GameActivity.EXTRA_VERBOSE, verbose)
        }
        startActivity(intent)
    }

    private fun refreshUiState() {
        val busy = isBusy.get()
        val hasSo = RecompPipeline.getBuiltGameSo(this).isFile
        val hasMod = GameModulePackager.getInstalledModuleSo(this) != null
        val upToDate = GameModulePackager.isModuleInstalledAndCurrent(this)
        val hasKit = File(filesDir, "kit/kit_manifest.json").isFile

        val abi = try {
            RecompPipeline.detectAbi(this).abi
        } catch (_: Throwable) {
            "unknown"
        }

        tvStatus.text = buildString {
            append("ABI: $abi | Kit: ${if (hasKit) "OK" else "pending"}")
            append(" | .so: ${if (hasSo) "OK" else "missing"}")
            append(" | Module: ${if (hasMod) (if (upToDate) "installed" else "update needed") else "not installed"}")
        }

        btnPickIso.isEnabled = !busy
        btnPrepare.text = if (busy) "Cancelar" else "Preparar"
        btnPrepare.isEnabled = true
        btnInstallModule.isEnabled = !busy && hasSo
        btnPlay.isEnabled = !busy && hasMod
    }

    private fun appendLog(line: String) {
        android.util.Log.i("EmotionalSenseLauncher", line)
        runOnUiThread {
            tvLog.append(line + "\n")
            svLog.post { svLog.fullScroll(ScrollView.FOCUS_DOWN) }
        }
    }
}
