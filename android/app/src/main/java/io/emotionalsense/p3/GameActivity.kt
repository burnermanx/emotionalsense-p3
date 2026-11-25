package io.emotionalsense.p3

import android.content.Context
import android.content.Intent
import android.hardware.input.InputManager
import android.os.Bundle
import android.view.InputDevice
import android.view.View
import android.view.ViewGroup
import android.widget.RelativeLayout
import org.libsdl.app.SDLActivity
import java.util.Locale

class GameActivity : SDLActivity(), InputManager.InputDeviceListener {

    companion object {
        const val EXTRA_RENDERER = "renderer"
        const val EXTRA_INTERNAL_SCALE = "internal_scale"
        const val EXTRA_TEX_FILTER = "tex_filter"
        const val EXTRA_BGA_SMOOTH = "bga_smooth"
        const val EXTRA_SUBPIXEL = "subpixel"
        const val EXTRA_CONFIRM = "confirm"
        const val EXTRA_FEVER = "fever"
        const val EXTRA_SPEED = "speed"
        const val EXTRA_SCORE = "score"
        const val EXTRA_BUTTONS = "buttons"
        const val EXTRA_TRIGGERS_2T = "triggers_2t"
        const val EXTRA_UNLOCK = "unlock"
        const val EXTRA_UNLOCK_ITEMS = "unlock_items"
        const val EXTRA_AUDIO_LATENCY_MS = "audio_latency_ms"
        const val EXTRA_VERBOSE = "verbose"
        const val EXTRA_TOUCH_MODE = "touch_mode"

        @JvmStatic
        external fun nativeSetTouchState(buttons: Int, lx: Int, ly: Int, triggers: Int)

        private fun safeSetTouchState(buttons: Int, lx: Int, ly: Int, triggers: Int) {
            try {
                nativeSetTouchState(buttons, lx, ly, triggers)
            } catch (_: UnsatisfiedLinkError) {
            }
        }
    }

    private var touchOverlay: TouchControlsOverlay? = null
    private var touchMode: String = "auto"
    private var inputManager: InputManager? = null

    override fun getLibraries(): Array<String> {
        return arrayOf("SDL2", "avutil", "avcodec")
    }

    override fun loadLibraries() {
        super.loadLibraries()
        val soFile = GameModulePackager.getInstalledModuleSo(this)
        if (soFile != null) {
            System.load(soFile.absolutePath)
        }
    }

    override fun getMainSharedObject(): String {
        val soFile = GameModulePackager.getInstalledModuleSo(this)
            ?: throw IllegalStateException(
                "Game module ${GameModulePackager.MODULE_PACKAGE} is not installed"
            )
        return soFile.absolutePath
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        touchMode = intent?.getStringExtra(EXTRA_TOUCH_MODE) ?: "auto"
        if (!mBrokenLibraries) {
            applyLaunchEnvironment()

            if (mLayout != null) {
                val overlay = TouchControlsOverlay(this).apply {
                    onStateChanged = { buttons, lx, ly, triggers ->
                        safeSetTouchState(buttons, lx, ly, triggers)
                    }
                    onSettingsClicked = {
                        openLauncherSettings()
                    }
                }
                touchOverlay = overlay
                mLayout.addView(
                    overlay,
                    RelativeLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT
                    )
                )
            }

            inputManager = (getSystemService(Context.INPUT_SERVICE) as? InputManager)?.also {
                it.registerInputDeviceListener(this, null)
            }
            updateTouchOverlayVisibility()
        }
    }

    private fun applyLaunchEnvironment() {
        val internalScale = intent?.getStringExtra(EXTRA_INTERNAL_SCALE) ?: "auto"
        nativeSetenv("EMOTIONALSENSE_INTERNAL_SCALE", internalScale)

        val bgaSmooth = intent?.getStringExtra(EXTRA_BGA_SMOOTH) ?: "0"
        nativeSetenv("EMOTIONALSENSE_BGA_SMOOTH", bgaSmooth)

        val subpixel = intent?.getBooleanExtra(EXTRA_SUBPIXEL, true) ?: true
        nativeSetenv("EMOTIONALSENSE_SUBPIXEL", if (subpixel) "1" else "0")

        val unlock = intent?.getBooleanExtra(EXTRA_UNLOCK, true) ?: true
        nativeSetenv("EMOTIONALSENSE_UNLOCK", if (unlock) "1" else "0")

        val unlockItems = intent?.getBooleanExtra(EXTRA_UNLOCK_ITEMS, true) ?: true
        nativeSetenv("EMOTIONALSENSE_UNLOCK_ITEMS", if (unlockItems) "1" else "0")

        val latencyMs = (intent?.getIntExtra(EXTRA_AUDIO_LATENCY_MS, 70) ?: 70).coerceIn(10, 500)
        nativeSetenv("EMOTIONALSENSE_AUDIO_LATENCY_MS", latencyMs.toString())

        if (intent?.getBooleanExtra(EXTRA_VERBOSE, false) == true) {
            nativeSetenv("EMOTIONALSENSE_LOG", "1")
        }

        val locale = Locale.getDefault()
        val lang = locale.language.orEmpty()
        if (lang.isNotEmpty()) {
            nativeSetenv("LANG", lang)
            val esLang = when (lang) {
                "pt" -> "pt-BR"
                "es", "ja", "ko", "en" -> lang
                else -> "en"
            }
            nativeSetenv("EMOTIONALSENSE_LANG", esLang)
        }
    }

    private fun openLauncherSettings() {
        touchOverlay?.resetState()
        safeSetTouchState(0, 128, 128, 0)
        val settingsIntent = Intent(this, LauncherActivity::class.java).apply {
            putExtra(LauncherActivity.EXTRA_OPEN_SETTINGS, true)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        }
        startActivity(settingsIntent)
        finish()
    }

    override fun onPause() {
        touchOverlay?.resetState()
        safeSetTouchState(0, 128, 128, 0)
        super.onPause()
    }

    override fun onDestroy() {
        inputManager?.unregisterInputDeviceListener(this)
        inputManager = null
        super.onDestroy()
    }

    override fun onInputDeviceAdded(deviceId: Int) {
        updateTouchOverlayVisibility()
    }

    override fun onInputDeviceRemoved(deviceId: Int) {
        updateTouchOverlayVisibility()
    }

    override fun onInputDeviceChanged(deviceId: Int) {
        updateTouchOverlayVisibility()
    }

    private fun hasPhysicalGamepad(): Boolean {
        for (id in InputDevice.getDeviceIds()) {
            val dev = InputDevice.getDevice(id) ?: continue
            if (dev.isVirtual) continue
            val src = dev.sources
            val isGamepad = (src and InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD
            val isJoystick = (src and InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK
            if (isGamepad || isJoystick) {
                return true
            }
        }
        return false
    }

    private fun updateTouchOverlayVisibility() {
        val overlay = touchOverlay ?: return
        val padVisible = when (touchMode) {
            "on" -> true
            "off" -> false
            else -> !hasPhysicalGamepad()
        }
        runOnUiThread {
            if (!padVisible) {
                safeSetTouchState(0, 128, 128, 0)
            }
            overlay.visibility = View.VISIBLE
            overlay.setPadEnabled(padVisible)
        }
    }

    override fun getArguments(): Array<String> {
        val renderer = intent?.getStringExtra(EXTRA_RENDERER) ?: "vulkan"
        val internalScale = intent?.getStringExtra(EXTRA_INTERNAL_SCALE) ?: "auto"
        val texFilter = intent?.getStringExtra(EXTRA_TEX_FILTER) ?: "auto"
        val confirm = intent?.getStringExtra(EXTRA_CONFIRM) ?: "auto"
        val fever = intent?.getStringExtra(EXTRA_FEVER) ?: "respect"
        val speed = intent?.getStringExtra(EXTRA_SPEED) ?: "respect"
        val score = intent?.getStringExtra(EXTRA_SCORE) ?: "respect"
        val buttons = intent?.getStringExtra(EXTRA_BUTTONS) ?: "auto"
        val triggers2t = intent?.getStringExtra(EXTRA_TRIGGERS_2T) ?: "stick"

        val args = mutableListOf(
            "--data", filesDir.absolutePath,
            "--renderer", renderer,
            "--tex-filter", texFilter,
            "--confirm", confirm,
            "--fever", fever,
            "--speed", speed,
            "--score", score,
            "--buttons", buttons,
            "--triggers-2t", triggers2t
        )
        if (internalScale != "auto") {
            args.add("--internal-scale")
            args.add(internalScale)
        }
        if (intent?.getBooleanExtra(EXTRA_VERBOSE, false) == true) {
            args.add("--verbose")
        }
        return args.toTypedArray()
    }
}
