package io.emotionalsense.p3

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BlurMaskFilter
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import kotlin.math.hypot

/**
 * Procedural dark stage background matching the PC launcher's es_theme.py aesthetic:
 * vertical stage gradient, diagonal translucent light beams, soft radial flare and vignette.
 */
class StageBackgroundView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    private var cachedBitmap: Bitmap? = null
    private var cachedW = 0
    private var cachedH = 0
    private val blitPaint = Paint(Paint.FILTER_BITMAP_FLAG)

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w <= 0 || h <= 0) return
        if (w != cachedW || h != cachedH) {
            cachedW = w
            cachedH = h
            cachedBitmap?.recycle()
            cachedBitmap = renderStageBitmap(w, h)
        }
    }

    override fun onDetachedFromWindow() {
        cachedBitmap?.recycle()
        cachedBitmap = null
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        val bmp = cachedBitmap
        if (bmp != null && !bmp.isRecycled) {
            canvas.drawBitmap(bmp, null, android.graphics.Rect(0, 0, width, height), blitPaint)
        } else {
            canvas.drawColor(Color.rgb(5, 7, 9))
        }
    }

    private fun renderStageBitmap(fullW: Int, fullH: Int): Bitmap {
        // Render at half resolution for soft Gaussian-like beams & radial flares at minimal cost
        val w = (fullW / 2).coerceAtLeast(64)
        val h = (fullH / 2).coerceAtLeast(64)
        val wf = w.toFloat()
        val hf = h.toFloat()

        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)

        // 1. Vertical stage falloff: #161A1E -> #080A0D -> #020304
        val bgPaint = Paint().apply {
            shader = LinearGradient(
                0f, 0f, 0f, hf,
                intArrayOf(
                    Color.rgb(22, 26, 30),
                    Color.rgb(8, 10, 13),
                    Color.rgb(2, 3, 4)
                ),
                floatArrayOf(0f, 0.5f, 1f),
                Shader.TileMode.CLAMP
            )
        }
        c.drawRect(0f, 0f, wf, hf, bgPaint)

        // 2. Diagonal translucent light beams
        data class BeamSpec(
            val x0: Float, val y0: Float,
            val x1: Float, val y1: Float,
            val w0: Float, val w1: Float,
            val alpha: Int, val r: Int, val g: Int, val b: Int
        )

        val scale = minOf(wf, hf) / 360f
        val beams = listOf(
            BeamSpec(wf * 0.78f, -24f, wf * 0.28f, hf + 24f, 6f * scale, 70f * scale, 42, 210, 230, 255),
            BeamSpec(wf * 0.95f, -24f, wf * 0.62f, hf + 24f, 4f * scale, 48f * scale, 30, 255, 220, 235),
            BeamSpec(wf * 0.52f, -24f, wf * 0.05f, hf + 24f, 3f * scale, 34f * scale, 22, 200, 255, 225),
            BeamSpec(wf * 1.08f, hf * 0.10f, wf * 0.72f, hf + 24f, 2f * scale, 22f * scale, 26, 255, 255, 255)
        )

        val beamPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
            maskFilter = BlurMaskFilter((8f * scale).coerceAtLeast(3f), BlurMaskFilter.Blur.NORMAL)
        }
        val path = Path()
        for (b in beams) {
            val dx = b.x1 - b.x0
            val dy = b.y1 - b.y0
            val len = hypot(dx, dy).coerceAtLeast(1f)
            val nx = -dy / len
            val ny = dx / len
            path.reset()
            path.moveTo(b.x0 + nx * b.w0, b.y0 + ny * b.w0)
            path.lineTo(b.x0 - nx * b.w0, b.y0 - ny * b.w0)
            path.lineTo(b.x1 - nx * b.w1, b.y1 - ny * b.w1)
            path.lineTo(b.x1 + nx * b.w1, b.y1 + ny * b.w1)
            path.close()
            beamPaint.color = Color.argb(b.alpha, b.r, b.g, b.b)
            c.drawPath(path, beamPaint)
        }

        // 3. Soft radial flare behind the header
        val fx = wf * 0.28f
        val fy = hf * 0.16f
        val flareSpecs = listOf(
            Triple(180f * scale, Color.rgb(150, 90, 200), 46),
            Triple(105f * scale, Color.rgb(255, 150, 170), 68),
            Triple(48f * scale, Color.rgb(255, 230, 220), 110)
        )
        val flarePaint = Paint(Paint.ANTI_ALIAS_FLAG)
        for ((rad, col, al) in flareSpecs) {
            val r = rad.coerceAtLeast(8f)
            flarePaint.shader = RadialGradient(
                fx, fy, r,
                intArrayOf(
                    Color.argb(al, Color.red(col), Color.green(col), Color.blue(col)),
                    Color.argb(0, Color.red(col), Color.green(col), Color.blue(col))
                ),
                floatArrayOf(0f, 1f),
                Shader.TileMode.CLAMP
            )
            c.drawCircle(fx, fy, r, flarePaint)
        }

        // Subtle diagonal lens streak
        val streakPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.argb(60, 255, 235, 235)
            strokeWidth = (1.5f * scale).coerceAtLeast(1f)
            maskFilter = BlurMaskFilter(2f, BlurMaskFilter.Blur.NORMAL)
        }
        c.drawLine(fx - 160f * scale, fy + 64f * scale, fx + 220f * scale, fy - 86f * scale, streakPaint)

        // 4. Subtle dark edge vignette
        val vigR = hypot(wf * 0.5f, hf * 0.5f).coerceAtLeast(16f)
        val vigPaint = Paint().apply {
            shader = RadialGradient(
                wf * 0.5f, hf * 0.45f, vigR,
                intArrayOf(
                    Color.argb(0, 0, 0, 0),
                    Color.argb(40, 0, 0, 0),
                    Color.argb(150, 0, 0, 0)
                ),
                floatArrayOf(0f, 0.65f, 1f),
                Shader.TileMode.CLAMP
            )
        }
        c.drawRect(0f, 0f, wf, hf, vigPaint)

        return bmp
    }
}
