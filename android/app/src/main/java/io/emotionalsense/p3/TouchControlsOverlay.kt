package io.emotionalsense.p3

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import kotlin.math.cos
import kotlin.math.sin

class TouchControlsOverlay @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    companion object {
        const val PSP_CTRL_SELECT = 0x0001
        const val PSP_CTRL_START = 0x0008
        const val PSP_CTRL_UP = 0x0010
        const val PSP_CTRL_RIGHT = 0x0020
        const val PSP_CTRL_DOWN = 0x0040
        const val PSP_CTRL_LEFT = 0x0080
        const val PSP_CTRL_LTRIGGER = 0x0100
        const val PSP_CTRL_RTRIGGER = 0x0200
        const val PSP_CTRL_TRIANGLE = 0x1000
        const val PSP_CTRL_CIRCLE = 0x2000
        const val PSP_CTRL_CROSS = 0x4000
        const val PSP_CTRL_SQUARE = 0x8000
    }

    private enum class Action {
        BUTTON,
        ANALOG_LEFT,
        ANALOG_RIGHT,
        TOGGLE_VISIBILITY,
        OPEN_SETTINGS
    }

    private data class Control(
        val id: String,
        val label: String,
        val action: Action,
        val bit: Int,
        val accentColor: Int,
        val isRound: Boolean,
        val rect: RectF = RectF(),
        val hitRect: RectF = RectF()
    )

    var onStateChanged: ((buttons: Int, lx: Int, ly: Int, triggers: Int) -> Unit)? = null
    var onSettingsClicked: (() -> Unit)? = null

    private var padEnabled = true
    private var controlsHidden = false
    private val activePointers = HashMap<Int, String>()
    private var lastButtons = -1
    private var lastLx = -1
    private var lastLy = -1
    private var lastTriggers = -1

    private val controls = listOf(
        Control("L", "L", Action.BUTTON, PSP_CTRL_LTRIGGER, Color.WHITE, isRound = false),
        Control("R", "R", Action.BUTTON, PSP_CTRL_RTRIGGER, Color.WHITE, isRound = false),
        Control("SELECT", "SELECT", Action.BUTTON, PSP_CTRL_SELECT, Color.LTGRAY, isRound = false),
        Control("START", "START", Action.BUTTON, PSP_CTRL_START, Color.LTGRAY, isRound = false),
        Control("UP", "▲", Action.BUTTON, PSP_CTRL_UP, Color.WHITE, isRound = false),
        Control("DOWN", "▼", Action.BUTTON, PSP_CTRL_DOWN, Color.WHITE, isRound = false),
        Control("LEFT", "◄", Action.BUTTON, PSP_CTRL_LEFT, Color.WHITE, isRound = false),
        Control("RIGHT", "►", Action.BUTTON, PSP_CTRL_RIGHT, Color.WHITE, isRound = false),
        Control("AL", "A◄", Action.ANALOG_LEFT, 0, Color.parseColor("#FF7A59"), isRound = true),
        Control("AR", "A►", Action.ANALOG_RIGHT, 0, Color.parseColor("#FF7A59"), isRound = true),
        Control("TRI", "△", Action.BUTTON, PSP_CTRL_TRIANGLE, Color.parseColor("#4CD9A6"), isRound = true),
        Control("CIR", "○", Action.BUTTON, PSP_CTRL_CIRCLE, Color.parseColor("#F06A6A"), isRound = true),
        Control("CRO", "✕", Action.BUTTON, PSP_CTRL_CROSS, Color.parseColor("#6BA8FF"), isRound = true),
        Control("SQU", "□", Action.BUTTON, PSP_CTRL_SQUARE, Color.parseColor("#D678E8"), isRound = true),
        Control("TOGGLE", "PAD", Action.TOGGLE_VISIBILITY, 0, Color.LTGRAY, isRound = false),
        Control("SETTINGS", "⚙", Action.OPEN_SETTINGS, 0, Color.LTGRAY, isRound = false)
    )

    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val gearPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        isFakeBoldText = true
    }

    fun setPadEnabled(enabled: Boolean) {
        if (padEnabled == enabled) return
        padEnabled = enabled
        if (!enabled) {
            activePointers.clear()
            emitState()
        }
        layoutControls(width.toFloat(), height.toFloat())
        invalidate()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        layoutControls(w.toFloat(), h.toFloat())
    }

    private fun layoutControls(w: Float, h: Float) {
        if (w <= 0f || h <= 0f) return
        val unit = minOf(w / 16f, h / 9f)
        val margin = unit * 0.55f
        val hitPad = unit * 0.18f

        fun setControl(id: String, cx: Float, cy: Float, bw: Float, bh: Float, pad: Float = hitPad) {
            val c = controls.first { it.id == id }
            c.rect.set(cx - bw / 2f, cy - bh / 2f, cx + bw / 2f, cy + bh / 2f)
            c.hitRect.set(c.rect.left - pad, c.rect.top - pad, c.rect.right + pad, c.rect.bottom + pad)
        }

        // Top-center visibility toggle + settings gear
        val topY = margin + unit * 0.35f
        val topH = unit * 0.65f
        if (padEnabled) {
            setControl("TOGGLE", w / 2f - unit * 0.45f, topY, unit * 1.35f, topH, unit * 0.12f)
            setControl("SETTINGS", w / 2f + unit * 0.66f, topY, unit * 0.72f, topH, unit * 0.12f)
        } else {
            setControl("TOGGLE", -1000f, -1000f, 0f, 0f, 0f)
            setControl("SETTINGS", w / 2f, topY, unit * 0.78f, topH, unit * 0.14f)
        }

        // Shoulders + Select/Start
        val shW = unit * 2.1f
        val shH = unit * 0.85f
        setControl("L", margin + shW / 2f, margin + shH / 2f, shW, shH)
        setControl("R", w - margin - shW / 2f, margin + shH / 2f, shW, shH)

        val sysW = unit * 1.45f
        val sysH = unit * 0.58f
        setControl("SELECT", margin + shW + unit * 0.35f + sysW / 2f, margin + shH / 2f, sysW, sysH)
        setControl("START", w - margin - shW - unit * 0.35f - sysW / 2f, margin + shH / 2f, sysW, sysH)

        // Left D-pad
        val dCx = margin + unit * 2.0f
        val dCy = h - margin - unit * 2.2f
        val dStep = unit * 1.25f
        val dSize = unit * 1.15f
        setControl("UP", dCx, dCy - dStep, dSize, dSize)
        setControl("DOWN", dCx, dCy + dStep, dSize, dSize)
        setControl("LEFT", dCx - dStep, dCy, dSize, dSize)
        setControl("RIGHT", dCx + dStep, dCy, dSize, dSize)

        // Horizontal analog buttons for DJMAX analog notes
        val aSize = unit * 1.05f
        val aY = h - margin - aSize / 2f
        setControl("AL", dCx + unit * 2.25f, aY, aSize, aSize)
        setControl("AR", dCx + unit * 3.55f, aY, aSize, aSize)

        // Right face buttons (PlayStation diamond)
        val fCx = w - margin - unit * 2.1f
        val fCy = h - margin - unit * 2.15f
        val fStep = unit * 1.32f
        val fSize = unit * 1.32f
        setControl("TRI", fCx, fCy - fStep, fSize, fSize)
        setControl("CRO", fCx, fCy + fStep, fSize, fSize)
        setControl("SQU", fCx - fStep, fCy, fSize, fSize)
        setControl("CIR", fCx + fStep, fCy, fSize, fSize)
    }

    private fun isControlVisible(c: Control): Boolean {
        if (!padEnabled) return c.action == Action.OPEN_SETTINGS
        if (controlsHidden) return c.action == Action.TOGGLE_VISIBILITY || c.action == Action.OPEN_SETTINGS
        return true
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val pressedIds = activePointers.values.toSet()
        val unit = minOf(width / 16f, height / 9f).coerceAtLeast(1f)
        strokePaint.strokeWidth = (unit * 0.045f).coerceAtLeast(2f)

        for (c in controls) {
            if (!isControlVisible(c)) continue
            val pressed = c.id in pressedIds
            val isTopBar = c.action == Action.TOGGLE_VISIBILITY || c.action == Action.OPEN_SETTINGS
            val baseAlpha = when {
                isTopBar && pressed -> 150
                isTopBar && (!padEnabled || controlsHidden) -> 55
                isTopBar -> 90
                pressed -> 135
                else -> 42
            }
            fillPaint.color = Color.argb(
                baseAlpha,
                Color.red(c.accentColor),
                Color.green(c.accentColor),
                Color.blue(c.accentColor)
            )
            strokePaint.color = Color.argb(
                if (pressed) 220 else if (isTopBar && (!padEnabled || controlsHidden)) 95 else 140,
                Color.red(c.accentColor),
                Color.green(c.accentColor),
                Color.blue(c.accentColor)
            )
            val corner = minOf(c.rect.width(), c.rect.height()) * 0.24f
            if (c.isRound) {
                val r = minOf(c.rect.width(), c.rect.height()) / 2f
                canvas.drawCircle(c.rect.centerX(), c.rect.centerY(), r, fillPaint)
                canvas.drawCircle(c.rect.centerX(), c.rect.centerY(), r, strokePaint)
            } else {
                canvas.drawRoundRect(c.rect, corner, corner, fillPaint)
                canvas.drawRoundRect(c.rect, corner, corner, strokePaint)
            }

            if (c.action == Action.OPEN_SETTINGS) {
                drawGearIcon(canvas, c.rect.centerX(), c.rect.centerY(), minOf(c.rect.width(), c.rect.height()) * 0.28f, pressed)
            } else {
                textPaint.color = Color.argb(if (pressed) 255 else 200, 255, 255, 255)
                textPaint.textSize = minOf(c.rect.height() * 0.44f, unit * 0.48f)
                val label = if (c.action == Action.TOGGLE_VISIBILITY && controlsHidden) "SHOW" else c.label
                val baseline = c.rect.centerY() - (textPaint.descent() + textPaint.ascent()) / 2f
                canvas.drawText(label, c.rect.centerX(), baseline, textPaint)
            }
        }
    }

    private fun drawGearIcon(canvas: Canvas, cx: Float, cy: Float, radius: Float, pressed: Boolean) {
        val alpha = if (pressed) 255 else if (!padEnabled || controlsHidden) 170 else 210
        gearPaint.color = Color.argb(alpha, 255, 255, 255)
        gearPaint.strokeWidth = (radius * 0.28f).coerceAtLeast(2f)
        gearPaint.strokeCap = Paint.Cap.ROUND
        canvas.drawCircle(cx, cy, radius * 0.56f, gearPaint)
        val inner = radius * 0.68f
        val outer = radius * 1.05f
        for (i in 0 until 8) {
            val angle = i * (Math.PI / 4.0)
            val cosA = cos(angle).toFloat()
            val sinA = sin(angle).toFloat()
            canvas.drawLine(cx + inner * cosA, cy + inner * sinA, cx + outer * cosA, cy + outer * sinA, gearPaint)
        }
    }

    private fun hitTest(x: Float, y: Float): Control? {
        var best: Control? = null
        var bestDist = Float.MAX_VALUE
        for (c in controls) {
            if (!isControlVisible(c)) continue
            if (c.hitRect.contains(x, y)) {
                val dx = x - c.rect.centerX()
                val dy = y - c.rect.centerY()
                val d = dx * dx + dy * dy
                if (d < bestDist) {
                    bestDist = d
                    best = c
                }
            }
        }
        return best
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        val action = event.actionMasked
        if ((!padEnabled || controlsHidden) && action == MotionEvent.ACTION_DOWN) {
            val hit = hitTest(event.getX(0), event.getY(0))
            if (hit == null) return false
        }

        when (action) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                val idx = event.actionIndex
                val pid = event.getPointerId(idx)
                val hit = hitTest(event.getX(idx), event.getY(idx))
                if (hit != null) {
                    if (hit.action == Action.TOGGLE_VISIBILITY) {
                        controlsHidden = !controlsHidden
                        if (controlsHidden) activePointers.clear()
                        emitState()
                        invalidate()
                        return true
                    }
                    activePointers[pid] = hit.id
                }
            }
            MotionEvent.ACTION_MOVE -> {
                for (i in 0 until event.pointerCount) {
                    val pid = event.getPointerId(i)
                    val cur = activePointers[pid]
                    if (cur == "TOGGLE") continue
                    val hit = hitTest(event.getX(i), event.getY(i))
                    if (cur == "SETTINGS") {
                        if (hit?.id != "SETTINGS") activePointers.remove(pid)
                    } else if (padEnabled && !controlsHidden) {
                        if (hit != null && hit.action != Action.TOGGLE_VISIBILITY && hit.action != Action.OPEN_SETTINGS) {
                            activePointers[pid] = hit.id
                        } else {
                            activePointers.remove(pid)
                        }
                    }
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP -> {
                val idx = event.actionIndex
                val pid = event.getPointerId(idx)
                val prevId = activePointers.remove(pid)
                if (prevId == "SETTINGS") {
                    val hit = hitTest(event.getX(idx), event.getY(idx))
                    if (hit?.id == "SETTINGS") {
                        emitState()
                        invalidate()
                        onSettingsClicked?.invoke()
                        return true
                    }
                }
            }
            MotionEvent.ACTION_CANCEL -> {
                activePointers.clear()
            }
        }

        emitState()
        invalidate()
        return true
    }

    fun resetState() {
        activePointers.clear()
        emitState()
        invalidate()
    }

    private fun emitState() {
        var buttons = 0
        var aLeft = false
        var aRight = false
        if (padEnabled && !controlsHidden) {
            val activeIds = activePointers.values.toSet()
            for (c in controls) {
                if (c.id !in activeIds) continue
                when (c.action) {
                    Action.BUTTON -> buttons = buttons or c.bit
                    Action.ANALOG_LEFT -> aLeft = true
                    Action.ANALOG_RIGHT -> aRight = true
                    Action.TOGGLE_VISIBILITY, Action.OPEN_SETTINGS -> {}
                }
            }
        }
        val lx = when {
            aLeft && !aRight -> 0
            aRight && !aLeft -> 255
            else -> 128
        }
        val ly = 128
        val triggers = 0
        if (buttons != lastButtons || lx != lastLx || ly != lastLy || triggers != lastTriggers) {
            lastButtons = buttons
            lastLx = lx
            lastLy = ly
            lastTriggers = triggers
            onStateChanged?.invoke(buttons, lx, ly, triggers)
        }
    }
}
