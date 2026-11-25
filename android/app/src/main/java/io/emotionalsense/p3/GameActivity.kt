package io.emotionalsense.p3

import android.content.Context
import android.hardware.input.InputManager
import android.os.Bundle
import android.view.InputDevice
import android.view.View
import android.view.ViewGroup
import android.widget.RelativeLayout
import org.libsdl.app.SDLActivity

class GameActivity : SDLActivity(), InputManager.InputDeviceListener {

    companion object {
        const val EXTRA_RENDERER = "renderer"
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
            val latencyMs = intent?.getIntExtra(EXTRA_AUDIO_LATENCY_MS, 70) ?: 70
            nativeSetenv("EMOTIONALSENSE_AUDIO_LATENCY_MS", latencyMs.toString())
            if (intent?.getBooleanExtra(EXTRA_VERBOSE, false) == true) {
                nativeSetenv("EMOTIONALSENSE_LOG", "1")
            }

            if (mLayout != null) {
                val overlay = TouchControlsOverlay(this).apply {
                    onStateChanged = { buttons, lx, ly, triggers ->
                        safeSetTouchState(buttons, lx, ly, triggers)
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
        val visible = when (touchMode) {
            "on" -> true
            "off" -> false
            else -> !hasPhysicalGamepad()
        }
        runOnUiThread {
            if (!visible) {
                overlay.resetState()
                safeSetTouchState(0, 128, 128, 0)
            }
            overlay.visibility = if (visible) View.VISIBLE else View.GONE
        }
    }

    override fun getArguments(): Array<String> {
        val renderer = intent?.getStringExtra(EXTRA_RENDERER) ?: "vulkan"
        val args = mutableListOf(
            "--data", filesDir.absolutePath,
            "--renderer", renderer
        )
        if (intent?.getBooleanExtra(EXTRA_VERBOSE, false) == true) {
            args.add("--verbose")
        }
        return args.toTypedArray()
    }
}
