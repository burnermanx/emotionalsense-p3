package io.emotionalsense.p3

import android.annotation.SuppressLint
import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.content.pm.PackageInstaller
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.ClipDrawable
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.LayerDrawable
import android.graphics.drawable.RippleDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.provider.Settings
import android.text.Editable
import android.text.InputType
import android.text.SpannableString
import android.text.Spanned
import android.text.TextWatcher
import android.text.style.ForegroundColorSpan
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

class LauncherActivity : Activity() {

    companion object {
        const val EXTRA_OPEN_SETTINGS = "open_settings"

        private const val REQ_PICK_ISO = 1001
        private const val REQ_INSTALL_PERM = 1002
        private const val PREFS_NAME = "es_prefs"

        private val COLOR_GREEN = Color.rgb(38, 214, 96)      // #26D660
        private val COLOR_GREEN_DARK = Color.rgb(24, 154, 68) // #189A44
        private val COLOR_LIME = Color.rgb(166, 255, 40)      // #A6FF28
        private val COLOR_CYAN = Color.rgb(40, 214, 255)      // #28D6FF
        private val COLOR_MAGENTA = Color.rgb(255, 54, 168)   // #FF36A8
        private val COLOR_AMBER = Color.rgb(245, 158, 11)     // #F59E0B
        private val COLOR_TEXT_PRIMARY = Color.parseColor("#F0F4F6")
        private val COLOR_TEXT_SECONDARY = Color.parseColor("#9AA4AE")
        private val COLOR_PANEL_BG = Color.parseColor("#CC0A0D10")
        private val COLOR_PANEL_BORDER = Color.parseColor("#2A343E")

        private val RENDERER_VALUES = listOf("vulkan", "software")
        private val RENDERER_LABELS = listOf("Vulkan (padrão)", "Software")

        private val SCALE_VALUES = listOf("auto", "1", "2", "3", "4")
        private val SCALE_LABELS = listOf("Auto (padrão)", "1x", "2x", "3x", "4x")

        private val TEX_FILTER_VALUES = listOf("auto", "linear", "nearest")
        private val TEX_FILTER_LABELS = listOf("Auto (padrão)", "Linear", "Nearest")

        private val BGA_VALUES = listOf("0", "blend")
        private val BGA_LABELS = listOf("Desligado (padrão)", "Suave 50% (blend)")

        private val CONFIRM_VALUES = listOf("auto", "cross", "circle")
        private val CONFIRM_LABELS = listOf("Auto (padrão)", "Cross (✕)", "Circle (○)")

        private val RESPECT_ORIG_VALUES = listOf("respect", "original")
        private val FEVER_LABELS = listOf("Respect (padrão)", "Original (PSP)")
        private val SPEED_LABELS = listOf("Respect (padrão)", "Original (PSP)")
        private val SCORE_LABELS = listOf("Respect (padrão)", "Original (PSP)")

        private val BUTTONS_VALUES = listOf("auto", "playstation", "xbox", "steamdeck", "switch")
        private val BUTTONS_LABELS = listOf("Auto (padrão)", "PlayStation", "Xbox", "Steam Deck", "Switch")

        private val TRIGGERS_VALUES = listOf("stick", "lr")
        private val TRIGGERS_LABELS = listOf("Analógico (laterais, padrão)", "L/R (velocidade)")

        private val TOUCH_MODE_VALUES = listOf("auto", "on", "off")
        private val TOUCH_MODE_LABELS = listOf("Auto (padrão)", "Sempre", "Desligado")
    }

    private lateinit var tvHeaderMeta: TextView
    private lateinit var bannerContainer: LinearLayout
    private lateinit var tvBanner: TextView
    private lateinit var tvStatus: TextView
    private lateinit var tvIso: TextView
    private lateinit var btnPickIso: Button
    private lateinit var btnPrepare: Button
    private lateinit var btnInstallModule: Button
    private lateinit var btnPlay: Button
    private lateinit var progressBar: ProgressBar
    private lateinit var tvProgress: TextView

    // Jogabilidade
    private lateinit var spConfirm: Spinner
    private lateinit var spFever: Spinner
    private lateinit var spSpeed: Spinner
    private lateinit var spScore: Spinner
    private lateinit var spButtons: Spinner
    private lateinit var spTriggers: Spinner
    private lateinit var cbUnlock: CheckBox
    private lateinit var cbUnlockItems: CheckBox

    // Gráficos & Áudio
    private lateinit var spRenderer: Spinner
    private lateinit var spInternalScale: Spinner
    private lateinit var spTexFilter: Spinner
    private lateinit var spBgaSmooth: Spinner
    private lateinit var cbSubpixel: CheckBox
    private lateinit var etLatency: EditText

    // Controles & Avançado
    private lateinit var spTouchMode: Spinner
    private lateinit var cbAutoLaunch: CheckBox
    private lateinit var cbVerbose: CheckBox
    private lateinit var btnRestoreDefaults: Button

    // Log
    private lateinit var btnToggleLog: Button
    private lateinit var logContainer: LinearLayout
    private lateinit var svLog: ScrollView
    private lateinit var tvLog: TextView
    private var logExpanded = false

    private var selectedIso: IsoExtractor.IsoSourceSpec? = null
    private val isBusy = AtomicBoolean(false)
    private val cancelRequested = AtomicBoolean(false)
    private var workerThread: Thread? = null
    private var pendingInstallAfterPerm = false
    private var receiverRegistered = false
    private var suppressPrefCallbacks = false

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
                    setLogExpanded(true)
                    isBusy.set(false)
                    refreshUiState()
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val openSettings = intent?.getBooleanExtra(EXTRA_OPEN_SETTINGS, false) == true
        val autoLaunch = prefs.getBoolean("auto_launch", true)
        if (savedInstanceState == null && !openSettings && autoLaunch) {
            val status = ModuleState.evaluate(this)
            if (status.readiness == ReadinessState.READY) {
                startActivity(buildGameIntent(prefs))
                finish()
                return
            }
        }

        buildUi()
        loadPrefsToUi(prefs)

        val filter = IntentFilter(GameModulePackager.ACTION_INSTALL_RESULT)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(installReceiver, filter, RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            registerReceiver(installReceiver, filter)
        }
        receiverRegistered = true

        detectLocalIso()
        initKitInBackground()
        refreshUiState()
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        if (intent != null) {
            setIntent(intent)
        }
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
        if (receiverRegistered) {
            unregisterReceiver(installReceiver)
            receiverRegistered = false
        }
        super.onDestroy()
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun boldItalicTypeface(): Typeface =
        Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD_ITALIC)

    private fun makeAccentPanel(
        accentColor: Int,
        bgColor: Int = COLOR_PANEL_BG,
        borderColor: Int = COLOR_PANEL_BORDER
    ): Drawable {
        val radius = dp(8).toFloat()
        val body = GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = radius
            setColor(bgColor)
            setStroke(dp(1), borderColor)
        }
        val bar = GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadii = floatArrayOf(radius, radius, 0f, 0f, 0f, 0f, radius, radius)
            setColor(accentColor)
        }
        return LayerDrawable(arrayOf<Drawable>(body, bar)).apply {
            setLayerInset(1, 0, 0, 0, 0)
            setLayerWidth(1, dp(4))
            setLayerGravity(1, Gravity.START or Gravity.FILL_VERTICAL)
        }
    }

    private fun makeMetallicHeaderBg(): Drawable {
        val metal = GradientDrawable(
            GradientDrawable.Orientation.TOP_BOTTOM,
            intArrayOf(
                Color.rgb(112, 118, 124),
                Color.rgb(66, 70, 76),
                Color.rgb(34, 38, 43),
                Color.rgb(18, 21, 25)
            )
        ).apply {
            cornerRadius = dp(8).toFloat()
            setStroke(dp(1), Color.parseColor("#44505C"))
        }
        val neonRule = GradientDrawable(
            GradientDrawable.Orientation.LEFT_RIGHT,
            intArrayOf(COLOR_CYAN, COLOR_GREEN, COLOR_LIME)
        )
        return LayerDrawable(arrayOf<Drawable>(metal, neonRule)).apply {
            setLayerHeight(1, dp(3))
            setLayerGravity(1, Gravity.BOTTOM or Gravity.FILL_HORIZONTAL)
            setLayerInset(1, dp(6), 0, dp(6), 0)
        }
    }

    private fun makeSecondaryButtonBg(borderColor: Int, enabled: Boolean = true): Drawable {
        val fill = GradientDrawable(
            GradientDrawable.Orientation.TOP_BOTTOM,
            if (enabled) {
                intArrayOf(Color.parseColor("#252D36"), Color.parseColor("#13171D"))
            } else {
                intArrayOf(Color.parseColor("#161A20"), Color.parseColor("#0F1216"))
            }
        ).apply {
            cornerRadius = dp(7).toFloat()
            setStroke(dp(1), if (enabled) borderColor else Color.parseColor("#232B33"))
        }
        val rippleColor = ColorStateList.valueOf(Color.argb(64, Color.red(borderColor), Color.green(borderColor), Color.blue(borderColor)))
        return RippleDrawable(rippleColor, fill, fill)
    }

    private fun makePlayButtonBg(enabled: Boolean): Drawable {
        val fill = if (enabled) {
            GradientDrawable(
                GradientDrawable.Orientation.LEFT_RIGHT,
                intArrayOf(COLOR_GREEN, COLOR_GREEN_DARK)
            ).apply {
                cornerRadius = dp(7).toFloat()
                setStroke(dp(1), COLOR_LIME)
            }
        } else {
            GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(Color.parseColor("#171E1B"), Color.parseColor("#101412"))
            ).apply {
                cornerRadius = dp(7).toFloat()
                setStroke(dp(1), Color.parseColor("#233028"))
            }
        }
        val rippleColor = ColorStateList.valueOf(Color.argb(90, 166, 255, 40))
        return RippleDrawable(rippleColor, fill, fill)
    }

    private fun makeGradientProgressDrawable(): Drawable {
        val track = GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(4).toFloat()
            setColor(Color.parseColor("#0A0D10"))
            setStroke(dp(1), COLOR_PANEL_BORDER)
        }
        val fill = GradientDrawable(
            GradientDrawable.Orientation.LEFT_RIGHT,
            intArrayOf(COLOR_CYAN, COLOR_GREEN, COLOR_LIME)
        ).apply {
            cornerRadius = dp(4).toFloat()
        }
        val clip = ClipDrawable(fill, Gravity.START, ClipDrawable.HORIZONTAL)
        return LayerDrawable(arrayOf<Drawable>(track, clip)).apply {
            setId(0, android.R.id.background)
            setId(1, android.R.id.progress)
        }
    }

    private fun makeSpinnerBg(): Drawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(6).toFloat()
            setColor(Color.parseColor("#151B22"))
            setStroke(dp(1), COLOR_PANEL_BORDER)
        }
    }

    private fun makeSpinnerAdapter(labels: List<String>): ArrayAdapter<String> {
        return object : ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, labels) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
                val v = super.getView(position, convertView, parent) as TextView
                v.setTextColor(COLOR_TEXT_PRIMARY)
                v.textSize = 13.5f
                v.setPadding(dp(10), dp(7), dp(22), dp(7))
                return v
            }

            override fun getDropDownView(position: Int, convertView: View?, parent: ViewGroup): View {
                val v = super.getDropDownView(position, convertView, parent) as TextView
                v.setTextColor(COLOR_TEXT_PRIMARY)
                v.setBackgroundColor(Color.parseColor("#141A21"))
                v.textSize = 14f
                v.setPadding(dp(14), dp(12), dp(14), dp(12))
                return v
            }
        }
    }

    private fun styleActionButton(btn: Button, label: String, borderColor: Int = Color.parseColor("#3A4654"), enabled: Boolean = true) {
        btn.text = label
        btn.isAllCaps = true
        btn.typeface = boldItalicTypeface()
        btn.textSize = 13f
        btn.isEnabled = enabled
        btn.setTextColor(if (enabled) COLOR_TEXT_PRIMARY else Color.parseColor("#5B6570"))
        btn.background = makeSecondaryButtonBg(borderColor, enabled)
        btn.setPadding(dp(12), dp(10), dp(12), dp(10))
        btn.minHeight = dp(42)
    }

    private fun addSectionCard(parent: LinearLayout, title: String, accentColor: Int = COLOR_CYAN): LinearLayout {
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = makeAccentPanel(accentColor)
            setPadding(dp(16), dp(12), dp(14), dp(12))
        }
        val header = TextView(this).apply {
            text = title.uppercase()
            textSize = 14f
            typeface = boldItalicTypeface()
            setTextColor(accentColor)
            setShadowLayer(4f, 0f, 1.5f, Color.argb(180, 0, 0, 0))
            setPadding(0, 0, 0, dp(8))
        }
        card.addView(header)
        val lp = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply {
            bottomMargin = dp(12)
        }
        parent.addView(card, lp)
        return card
    }

    private fun addSpinnerRow(card: LinearLayout, label: String, labels: List<String>, onChanged: (Int) -> Unit): Spinner {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(4), 0, dp(4))
        }
        val tv = TextView(this).apply {
            text = label
            textSize = 13.5f
            setTextColor(COLOR_TEXT_PRIMARY)
        }
        val sp = Spinner(this).apply {
            adapter = makeSpinnerAdapter(labels)
            background = makeSpinnerBg()
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (!suppressPrefCallbacks) onChanged(position)
                }

                override fun onNothingSelected(parent: AdapterView<*>?) {}
            }
        }
        row.addView(tv, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.05f).apply { marginEnd = dp(8) })
        row.addView(sp, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        card.addView(row)
        return sp
    }

    private fun addCheckBoxRow(card: LinearLayout, label: String, onChanged: (Boolean) -> Unit): CheckBox {
        val tint = ColorStateList(
            arrayOf(
                intArrayOf(android.R.attr.state_checked),
                intArrayOf()
            ),
            intArrayOf(COLOR_GREEN, Color.parseColor("#788084"))
        )
        return CheckBox(this).apply {
            text = label
            textSize = 13.5f
            setTextColor(COLOR_TEXT_PRIMARY)
            buttonTintList = tint
            setPadding(dp(4), dp(5), 0, dp(5))
            setOnCheckedChangeListener { _, isChecked ->
                if (!suppressPrefCallbacks) onChanged(isChecked)
            }
            card.addView(this)
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun buildUi() {
        val stageFrame = FrameLayout(this)
        val stageBg = StageBackgroundView(this)
        stageFrame.addView(
            stageBg,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
        )

        val outerScroll = ScrollView(this).apply {
            setBackgroundColor(Color.TRANSPARENT)
            isFillViewport = true
        }
        stageFrame.addView(
            outerScroll,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
        )

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(20))
        }
        outerScroll.addView(root)

        // Metallic Header Strip matching es_theme.py
        val headerStrip = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = makeMetallicHeaderBg()
            setPadding(dp(14), dp(10), dp(14), dp(12))
        }

        val titleRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val titleText = SpannableString("EMOTIONAL SENSE-P3").apply {
            val split = "EMOTIONAL SENSE-".length
            setSpan(ForegroundColorSpan(COLOR_TEXT_PRIMARY), 0, split, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            setSpan(ForegroundColorSpan(COLOR_CYAN), split, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        val tvTitle = TextView(this).apply {
            text = titleText
            textSize = 20f
            typeface = boldItalicTypeface()
            setShadowLayer(6f, 1f, 2f, Color.argb(200, 0, 0, 0))
        }
        val badgeAndroid = TextView(this).apply {
            text = "ANDROID"
            textSize = 10.5f
            typeface = boldItalicTypeface()
            setTextColor(COLOR_LIME)
            setPadding(dp(8), dp(3), dp(8), dp(3))
            background = GradientDrawable().apply {
                cornerRadius = dp(4).toFloat()
                setColor(Color.parseColor("#11171D"))
                setStroke(dp(1), COLOR_CYAN)
            }
        }
        titleRow.addView(tvTitle, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        titleRow.addView(badgeAndroid)
        headerStrip.addView(titleRow)

        tvHeaderMeta = TextView(this).apply {
            textSize = 11.5f
            setTextColor(Color.parseColor("#C2CBD4"))
            setPadding(0, dp(3), 0, 0)
        }
        headerStrip.addView(tvHeaderMeta)

        root.addView(
            headerStrip,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(12) }
        )

        // Prominent readiness banner
        bannerContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = makeAccentPanel(COLOR_CYAN)
            setPadding(dp(16), dp(12), dp(14), dp(12))
        }
        tvBanner = TextView(this).apply {
            textSize = 14f
            typeface = boldItalicTypeface()
            setTextColor(COLOR_TEXT_PRIMARY)
            setShadowLayer(4f, 0f, 1.5f, Color.argb(180, 0, 0, 0))
        }
        bannerContainer.addView(tvBanner)
        root.addView(
            bannerContainer,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(12) }
        )

        // 1. Status / Preparação
        val prepCard = addSectionCard(root, "Status / Preparação", COLOR_GREEN)

        tvStatus = TextView(this).apply {
            textSize = 12f
            setTextColor(COLOR_TEXT_SECONDARY)
            setPadding(0, 0, 0, dp(6))
        }
        prepCard.addView(tvStatus)

        tvIso = TextView(this).apply {
            text = "ISO: nenhuma selecionada"
            textSize = 13f
            setTextColor(COLOR_TEXT_PRIMARY)
            setPadding(0, 0, 0, dp(10))
        }
        prepCard.addView(tvIso)

        val row1 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        btnPickIso = Button(this).apply {
            setOnClickListener { pickIso() }
        }
        btnPrepare = Button(this).apply {
            setOnClickListener {
                if (isBusy.get()) {
                    cancelRequested.set(true)
                    appendLog("Cancelamento solicitado...")
                } else {
                    startPrepare()
                }
            }
        }
        styleActionButton(btnPickIso, "Selecionar ISO", COLOR_CYAN)
        styleActionButton(btnPrepare, "Preparar", COLOR_CYAN)
        row1.addView(btnPickIso, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(5) })
        row1.addView(btnPrepare, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(5) })
        prepCard.addView(row1)

        val row2 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(8), 0, 0)
        }
        btnInstallModule = Button(this).apply {
            setOnClickListener { startInstallModule() }
        }
        btnPlay = Button(this).apply {
            setOnClickListener { launchGame() }
        }
        styleActionButton(btnInstallModule, "Instalar Módulo", COLOR_CYAN)
        row2.addView(btnInstallModule, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(5) })
        row2.addView(btnPlay, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(5) })
        prepCard.addView(row2)

        progressBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 1000
            progress = 0
            progressDrawable = makeGradientProgressDrawable()
        }
        prepCard.addView(
            progressBar,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(8)
            ).apply { topMargin = dp(12) }
        )

        tvProgress = TextView(this).apply {
            text = "Ocioso"
            textSize = 12f
            setTextColor(COLOR_TEXT_SECONDARY)
            setPadding(0, dp(5), 0, 0)
        }
        prepCard.addView(tvProgress)

        // 2. Jogabilidade
        val gameCard = addSectionCard(root, "Jogabilidade", COLOR_CYAN)
        spConfirm = addSpinnerRow(gameCard, "Botão de confirmação", CONFIRM_LABELS) { pos ->
            savePref { putString("confirm", CONFIRM_VALUES.getOrElse(pos) { "auto" }) }
        }
        spFever = addSpinnerRow(gameCard, "Modo Fever", FEVER_LABELS) { pos ->
            savePref { putString("fever", RESPECT_ORIG_VALUES.getOrElse(pos) { "respect" }) }
        }
        spSpeed = addSpinnerRow(gameCard, "Velocidade (Auto Velocity)", SPEED_LABELS) { pos ->
            savePref { putString("speed", RESPECT_ORIG_VALUES.getOrElse(pos) { "respect" }) }
        }
        spScore = addSpinnerRow(gameCard, "Pontuação (Score)", SCORE_LABELS) { pos ->
            savePref { putString("score", RESPECT_ORIG_VALUES.getOrElse(pos) { "respect" }) }
        }
        spButtons = addSpinnerRow(gameCard, "Símbolos dos botões", BUTTONS_LABELS) { pos ->
            savePref { putString("buttons", BUTTONS_VALUES.getOrElse(pos) { "auto" }) }
        }
        spTriggers = addSpinnerRow(gameCard, "Gatilhos nos modos .2T", TRIGGERS_LABELS) { pos ->
            savePref { putString("triggers_2t", TRIGGERS_VALUES.getOrElse(pos) { "stick" }) }
        }
        cbUnlock = addCheckBoxRow(gameCard, "Desbloquear músicas e modos") { checked ->
            savePref { putBoolean("unlock", checked) }
        }
        cbUnlockItems = addCheckBoxRow(gameCard, "Desbloquear colecionáveis (crews / gear / notas)") { checked ->
            savePref { putBoolean("unlock_items", checked) }
        }

        // 3. Gráficos & Áudio
        val gfxCard = addSectionCard(root, "Gráficos & Áudio", COLOR_CYAN)
        spRenderer = addSpinnerRow(gfxCard, "Renderer", RENDERER_LABELS) { pos ->
            savePref { putString("renderer", RENDERER_VALUES.getOrElse(pos) { "vulkan" }) }
        }
        spInternalScale = addSpinnerRow(gfxCard, "Resolução interna", SCALE_LABELS) { pos ->
            savePref { putString("internal_scale", SCALE_VALUES.getOrElse(pos) { "auto" }) }
        }
        spTexFilter = addSpinnerRow(gfxCard, "Filtro de texturas", TEX_FILTER_LABELS) { pos ->
            savePref { putString("tex_filter", TEX_FILTER_VALUES.getOrElse(pos) { "auto" }) }
        }
        spBgaSmooth = addSpinnerRow(gfxCard, "Suavização de BGA", BGA_LABELS) { pos ->
            savePref { putString("bga_smooth", BGA_VALUES.getOrElse(pos) { "0" }) }
        }
        cbSubpixel = addCheckBoxRow(gfxCard, "Subpixel das lanes") { checked ->
            savePref { putBoolean("subpixel", checked) }
        }

        val latRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(6), 0, 0)
        }
        val lblLat = TextView(this).apply {
            text = "Latência de áudio (ms)"
            textSize = 13.5f
            setTextColor(COLOR_TEXT_PRIMARY)
        }
        etLatency = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER
            setTextColor(COLOR_TEXT_PRIMARY)
            setHintTextColor(COLOR_TEXT_SECONDARY)
            hint = "70"
            background = makeSpinnerBg()
            setPadding(dp(10), dp(7), dp(10), dp(7))
            textSize = 13.5f
            addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
                override fun afterTextChanged(s: Editable?) {
                    if (suppressPrefCallbacks) return
                    val v = s?.toString()?.toIntOrNull()
                    if (v != null && v in 10..500) {
                        savePref { putInt("audio_latency_ms", v) }
                    }
                }
            })
        }
        latRow.addView(lblLat, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.05f).apply { marginEnd = dp(8) })
        latRow.addView(etLatency, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        gfxCard.addView(latRow)

        // 4. Controles & Avançado
        val advCard = addSectionCard(root, "Controles & Avançado", COLOR_CYAN)
        spTouchMode = addSpinnerRow(advCard, "Controles na tela", TOUCH_MODE_LABELS) { pos ->
            savePref { putString("touch_mode", TOUCH_MODE_VALUES.getOrElse(pos) { "auto" }) }
        }
        cbAutoLaunch = addCheckBoxRow(advCard, "Abrir jogo automaticamente ao iniciar (quando pronto)") { checked ->
            savePref { putBoolean("auto_launch", checked) }
        }
        cbVerbose = addCheckBoxRow(advCard, "Log detalhado (--verbose)") { checked ->
            savePref { putBoolean("verbose", checked) }
        }
        btnRestoreDefaults = Button(this).apply {
            setOnClickListener { restoreDefaults() }
        }
        styleActionButton(btnRestoreDefaults, "Restaurar padrões", COLOR_PANEL_BORDER)
        advCard.addView(
            btnRestoreDefaults,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(8) }
        )

        // 5. Área de Log expansível / rolável
        val logCard = addSectionCard(root, "Diagnóstico", Color.parseColor("#788084"))
        btnToggleLog = Button(this).apply {
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            setOnClickListener { setLogExpanded(!logExpanded) }
        }
        styleActionButton(btnToggleLog, "▸ Log de execução", COLOR_PANEL_BORDER)
        logCard.addView(btnToggleLog)

        logContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            setPadding(0, dp(8), 0, 0)
        }
        tvLog = TextView(this).apply {
            typeface = Typeface.MONOSPACE
            textSize = 11f
            setTextColor(Color.parseColor("#C9D1D9"))
        }
        svLog = ScrollView(this).apply {
            background = GradientDrawable().apply {
                cornerRadius = dp(6).toFloat()
                setColor(Color.parseColor("#E6070A0D"))
                setStroke(dp(1), COLOR_PANEL_BORDER)
            }
            setPadding(dp(8), dp(8), dp(8), dp(8))
            addView(tvLog)
            setOnTouchListener { v, event ->
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN, MotionEvent.ACTION_MOVE ->
                        v.parent?.requestDisallowInterceptTouchEvent(true)
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL ->
                        v.parent?.requestDisallowInterceptTouchEvent(false)
                }
                false
            }
        }
        logContainer.addView(
            svLog,
            LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(220))
        )
        logCard.addView(logContainer)

        setContentView(stageFrame)
    }

    private fun setLogExpanded(expanded: Boolean) {
        logExpanded = expanded
        logContainer.visibility = if (expanded) View.VISIBLE else View.GONE
        btnToggleLog.text = if (expanded) "▾ LOG DE EXECUÇÃO" else "▸ LOG DE EXECUÇÃO"
        if (expanded) {
            svLog.post { svLog.fullScroll(ScrollView.FOCUS_DOWN) }
        }
    }

    private fun savePref(block: SharedPreferences.Editor.() -> Unit) {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit().apply(block).apply()
    }

    private fun loadPrefsToUi(prefs: SharedPreferences) {
        suppressPrefCallbacks = true
        try {
            fun selectValue(sp: Spinner, values: List<String>, value: String) {
                val idx = values.indexOf(value)
                sp.setSelection(if (idx >= 0) idx else 0, false)
            }

            selectValue(spConfirm, CONFIRM_VALUES, prefs.getString("confirm", "auto") ?: "auto")
            selectValue(spFever, RESPECT_ORIG_VALUES, prefs.getString("fever", "respect") ?: "respect")
            selectValue(spSpeed, RESPECT_ORIG_VALUES, prefs.getString("speed", "respect") ?: "respect")
            selectValue(spScore, RESPECT_ORIG_VALUES, prefs.getString("score", "respect") ?: "respect")
            selectValue(spButtons, BUTTONS_VALUES, prefs.getString("buttons", "auto") ?: "auto")
            selectValue(spTriggers, TRIGGERS_VALUES, prefs.getString("triggers_2t", "stick") ?: "stick")
            cbUnlock.isChecked = prefs.getBoolean("unlock", true)
            cbUnlockItems.isChecked = prefs.getBoolean("unlock_items", true)

            selectValue(spRenderer, RENDERER_VALUES, prefs.getString("renderer", "vulkan") ?: "vulkan")
            selectValue(spInternalScale, SCALE_VALUES, prefs.getString("internal_scale", "auto") ?: "auto")
            selectValue(spTexFilter, TEX_FILTER_VALUES, prefs.getString("tex_filter", "auto") ?: "auto")
            selectValue(spBgaSmooth, BGA_VALUES, prefs.getString("bga_smooth", "0") ?: "0")
            cbSubpixel.isChecked = prefs.getBoolean("subpixel", true)
            etLatency.setText(prefs.getInt("audio_latency_ms", 70).coerceIn(10, 500).toString())

            selectValue(spTouchMode, TOUCH_MODE_VALUES, prefs.getString("touch_mode", "auto") ?: "auto")
            cbAutoLaunch.isChecked = prefs.getBoolean("auto_launch", true)
            cbVerbose.isChecked = prefs.getBoolean("verbose", false)
        } finally {
            suppressPrefCallbacks = false
        }
    }

    private fun persistAllUiToPrefs(): SharedPreferences {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val latency = etLatency.text.toString().toIntOrNull()?.coerceIn(10, 500) ?: 70
        if (etLatency.text.toString() != latency.toString()) {
            suppressPrefCallbacks = true
            etLatency.setText(latency.toString())
            suppressPrefCallbacks = false
        }
        prefs.edit()
            .putString("confirm", CONFIRM_VALUES.getOrElse(spConfirm.selectedItemPosition) { "auto" })
            .putString("fever", RESPECT_ORIG_VALUES.getOrElse(spFever.selectedItemPosition) { "respect" })
            .putString("speed", RESPECT_ORIG_VALUES.getOrElse(spSpeed.selectedItemPosition) { "respect" })
            .putString("score", RESPECT_ORIG_VALUES.getOrElse(spScore.selectedItemPosition) { "respect" })
            .putString("buttons", BUTTONS_VALUES.getOrElse(spButtons.selectedItemPosition) { "auto" })
            .putString("triggers_2t", TRIGGERS_VALUES.getOrElse(spTriggers.selectedItemPosition) { "stick" })
            .putBoolean("unlock", cbUnlock.isChecked)
            .putBoolean("unlock_items", cbUnlockItems.isChecked)
            .putString("renderer", RENDERER_VALUES.getOrElse(spRenderer.selectedItemPosition) { "vulkan" })
            .putString("internal_scale", SCALE_VALUES.getOrElse(spInternalScale.selectedItemPosition) { "auto" })
            .putString("tex_filter", TEX_FILTER_VALUES.getOrElse(spTexFilter.selectedItemPosition) { "auto" })
            .putString("bga_smooth", BGA_VALUES.getOrElse(spBgaSmooth.selectedItemPosition) { "0" })
            .putBoolean("subpixel", cbSubpixel.isChecked)
            .putInt("audio_latency_ms", latency)
            .putString("touch_mode", TOUCH_MODE_VALUES.getOrElse(spTouchMode.selectedItemPosition) { "auto" })
            .putBoolean("auto_launch", cbAutoLaunch.isChecked)
            .putBoolean("verbose", cbVerbose.isChecked)
            .apply()
        return prefs
    }

    private fun restoreDefaults() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        prefs.edit().clear().apply()
        loadPrefsToUi(prefs)
        appendLog("Configurações restauradas para o padrão.")
    }

    private fun buildGameIntent(prefs: SharedPreferences): Intent {
        return Intent(this, GameActivity::class.java).apply {
            putExtra(GameActivity.EXTRA_RENDERER, prefs.getString("renderer", "vulkan") ?: "vulkan")
            putExtra(GameActivity.EXTRA_INTERNAL_SCALE, prefs.getString("internal_scale", "auto") ?: "auto")
            putExtra(GameActivity.EXTRA_TEX_FILTER, prefs.getString("tex_filter", "auto") ?: "auto")
            putExtra(GameActivity.EXTRA_BGA_SMOOTH, prefs.getString("bga_smooth", "0") ?: "0")
            putExtra(GameActivity.EXTRA_SUBPIXEL, prefs.getBoolean("subpixel", true))
            putExtra(GameActivity.EXTRA_CONFIRM, prefs.getString("confirm", "auto") ?: "auto")
            putExtra(GameActivity.EXTRA_FEVER, prefs.getString("fever", "respect") ?: "respect")
            putExtra(GameActivity.EXTRA_SPEED, prefs.getString("speed", "respect") ?: "respect")
            putExtra(GameActivity.EXTRA_SCORE, prefs.getString("score", "respect") ?: "respect")
            putExtra(GameActivity.EXTRA_BUTTONS, prefs.getString("buttons", "auto") ?: "auto")
            putExtra(GameActivity.EXTRA_TRIGGERS_2T, prefs.getString("triggers_2t", "stick") ?: "stick")
            putExtra(GameActivity.EXTRA_UNLOCK, prefs.getBoolean("unlock", true))
            putExtra(GameActivity.EXTRA_UNLOCK_ITEMS, prefs.getBoolean("unlock_items", true))
            putExtra(GameActivity.EXTRA_AUDIO_LATENCY_MS, prefs.getInt("audio_latency_ms", 70).coerceIn(10, 500))
            putExtra(GameActivity.EXTRA_TOUCH_MODE, prefs.getString("touch_mode", "auto") ?: "auto")
            putExtra(GameActivity.EXTRA_VERBOSE, prefs.getBoolean("verbose", false))
        }
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
            tvIso.text = "ISO: disco extraído pronto em filesDir/disc"
        } else {
            tvIso.text = "ISO: nenhuma selecionada (coloque em ${extDir.absolutePath} ou selecione via SAF)"
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
            appendLog("Selecione uma ISO/CSO/ZSO primeiro.")
            setLogExpanded(true)
            return
        }
        if (!isBusy.compareAndSet(false, true)) return
        cancelRequested.set(false)
        setLogExpanded(true)
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
            appendLog("Execute Preparar primeiro para compilar libemotionalsense_game.so.")
            setLogExpanded(true)
            return
        }
        if (Build.VERSION.SDK_INT >= 26 && !packageManager.canRequestPackageInstalls()) {
            pendingInstallAfterPerm = true
            appendLog("Conceda a permissão 'Instalar apps desconhecidos' para EmotionalSense-P3...")
            setLogExpanded(true)
            val permIntent = Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:$packageName")
            )
            @Suppress("DEPRECATION")
            startActivityForResult(permIntent, REQ_INSTALL_PERM)
            return
        }
        if (!isBusy.compareAndSet(false, true)) return
        setLogExpanded(true)
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
        val status = ModuleState.evaluate(this)
        if (status.readiness != ReadinessState.READY) {
            appendLog("O módulo não está pronto (${status.readiness}).")
            refreshUiState()
            return
        }
        val prefs = persistAllUiToPrefs()
        startActivity(buildGameIntent(prefs))
    }

    private fun refreshUiState() {
        val busy = isBusy.get()
        val status = ModuleState.evaluate(this)
        val hasKit = File(filesDir, "kit/kit_manifest.json").isFile

        val abi = try {
            RecompPipeline.detectAbi(this).abi
        } catch (_: Throwable) {
            "unknown"
        }

        tvHeaderMeta.text = "v${BuildConfig.VERSION_NAME} • ABI: $abi • kit ${status.shortStamp}"

        tvStatus.text = buildString {
            append("Kit: ${if (hasKit) "OK (${status.shortStamp})" else "extraindo..."}")
            append(" | Disco: ${if (status.discReady) "OK" else "ausente"}")
            append(" | .so: ${if (status.preparedSoExists) "OK" else "ausente"}")
            append(" | Módulo: ")
            if (status.installedSoExists) {
                append("v${status.installedVersionCode}")
            } else if (status.installedPackagePresent) {
                append("incompleto")
            } else {
                append("não instalado")
            }
        }

        when (status.readiness) {
            ReadinessState.NEEDS_ISO -> {
                tvBanner.text = "Selecione a ISO/CSO/ZSO de DJMAX Portable 3 e clique em Preparar."
                tvBanner.setTextColor(Color.parseColor("#FDE68A"))
                bannerContainer.background = makeAccentPanel(
                    COLOR_AMBER,
                    Color.parseColor("#CC1B150A"),
                    Color.parseColor("#665020")
                )
            }
            ReadinessState.NEEDS_PREPARE_FIRST -> {
                tvBanner.text = "Disco extraído encontrado. Clique em Preparar para compilar o módulo."
                tvBanner.setTextColor(COLOR_CYAN)
                bannerContainer.background = makeAccentPanel(
                    COLOR_CYAN,
                    Color.parseColor("#CC0A1926"),
                    Color.parseColor("#244E6B")
                )
            }
            ReadinessState.NEEDS_REPREPARE_LAUNCHER_UPDATED -> {
                tvBanner.text = "O launcher foi atualizado. É necessário preparar e reinstalar o módulo do jogo novamente."
                tvBanner.setTextColor(Color.parseColor("#FFD1EC"))
                bannerContainer.background = makeAccentPanel(
                    COLOR_MAGENTA,
                    Color.parseColor("#CC260B1D"),
                    Color.parseColor("#8A245D")
                )
            }
            ReadinessState.NEEDS_INSTALL -> {
                tvBanner.text = "Módulo pronto (v${status.targetVersionCode}). Clique em Instalar Módulo para concluir."
                tvBanner.setTextColor(COLOR_CYAN)
                bannerContainer.background = makeAccentPanel(
                    COLOR_CYAN,
                    Color.parseColor("#CC0A1926"),
                    Color.parseColor("#28668C")
                )
            }
            ReadinessState.READY -> {
                tvBanner.text = "Pronto para jogar (kit ${status.shortStamp})"
                tvBanner.setTextColor(COLOR_LIME)
                bannerContainer.background = makeAccentPanel(
                    COLOR_GREEN,
                    Color.parseColor("#CC091D12"),
                    Color.parseColor("#1D6B3B")
                )
            }
        }

        val highlightPrepare = !busy && (
            status.readiness == ReadinessState.NEEDS_PREPARE_FIRST ||
                status.readiness == ReadinessState.NEEDS_REPREPARE_LAUNCHER_UPDATED
            )
        val highlightInstall = !busy && status.readiness == ReadinessState.NEEDS_INSTALL
        val playReady = !busy && status.readiness == ReadinessState.READY

        styleActionButton(
            btnPickIso,
            "Selecionar ISO",
            COLOR_CYAN,
            enabled = !busy
        )

        val prepareBorder = when {
            busy -> COLOR_MAGENTA
            status.readiness == ReadinessState.NEEDS_REPREPARE_LAUNCHER_UPDATED -> COLOR_MAGENTA
            highlightPrepare -> COLOR_GREEN
            else -> COLOR_CYAN
        }
        styleActionButton(
            btnPrepare,
            if (busy) "Cancelar" else "Preparar",
            prepareBorder,
            enabled = true
        )
        if (highlightPrepare && !busy) {
            btnPrepare.setTextColor(
                if (status.readiness == ReadinessState.NEEDS_REPREPARE_LAUNCHER_UPDATED) Color.parseColor("#FF8FD1")
                else COLOR_LIME
            )
        }

        val installEnabled = !busy &&
            status.preparedSoExists &&
            status.readiness != ReadinessState.NEEDS_ISO &&
            status.readiness != ReadinessState.NEEDS_REPREPARE_LAUNCHER_UPDATED
        styleActionButton(
            btnInstallModule,
            "Instalar Módulo",
            if (highlightInstall) COLOR_LIME else COLOR_CYAN,
            enabled = installEnabled
        )
        if (highlightInstall) {
            btnInstallModule.setTextColor(COLOR_CYAN)
        }

        btnPlay.text = "JOGAR"
        btnPlay.isAllCaps = true
        btnPlay.typeface = boldItalicTypeface()
        btnPlay.textSize = 14f
        btnPlay.isEnabled = playReady
        btnPlay.background = makePlayButtonBg(playReady)
        btnPlay.setPadding(dp(12), dp(10), dp(12), dp(10))
        btnPlay.minHeight = dp(42)
        if (playReady) {
            btnPlay.setTextColor(Color.WHITE)
            btnPlay.setShadowLayer(4f, 0f, 1.5f, Color.argb(210, 0, 0, 0))
        } else {
            btnPlay.setTextColor(Color.parseColor("#54625A"))
            btnPlay.setShadowLayer(0f, 0f, 0f, 0)
        }
    }

    private fun appendLog(line: String) {
        android.util.Log.i("EmotionalSenseLauncher", line)
        runOnUiThread {
            tvLog.append(line + "\n")
            svLog.post { svLog.fullScroll(ScrollView.FOCUS_DOWN) }
        }
    }
}
