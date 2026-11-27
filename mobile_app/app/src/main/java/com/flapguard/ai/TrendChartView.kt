package com.flapguard.ai

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.View
import kotlin.math.max
import kotlin.math.min

class TrendChartView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private var points: List<ReadingPoint> = emptyList()

    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#E2E8F0")
        strokeWidth = 1.5f
    }
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#64748B")
        textSize = 26f
    }
    private val legendPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#334155")
        textSize = 25f
    }
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 5f
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    fun setReadings(readings: List<ReadingPoint>) {
        points = readings
        invalidate()
    }

    private fun clamp(v: Double): Float = max(0.0, min(100.0, v)).toFloat()

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return

        val padLeft = 64f
        val padRight = 24f
        val padTop = 54f
        val padBottom = 42f
        val plotW = w - padLeft - padRight
        val plotH = h - padTop - padBottom

        // Legend row
        val seriesMeta = listOf(
            Triple("SpO₂", Color.parseColor("#2563EB")) { p: ReadingPoint -> clamp(p.spo2Pct) },
            Triple("Risk%", Color.parseColor("#E11D48")) { p: ReadingPoint -> clamp(p.riskScorePct) },
            Triple("Dopp×3", Color.parseColor("#0D9488")) { p: ReadingPoint -> clamp(p.dopplerCmS * 3.0) },
            Triple("ΔT×20", Color.parseColor("#D97706")) { p: ReadingPoint -> clamp(p.tempDeltaC * 20.0) }
        )

        var lx = padLeft
        for ((label, col, _) in seriesMeta) {
            dotPaint.color = col
            canvas.drawCircle(lx + 8f, 24f, 8f, dotPaint)
            canvas.drawText(label, lx + 22f, 32f, legendPaint)
            lx += max(128f, legendPaint.measureText(label) + 46f)
        }

        // Horizontal grid lines (0..100)
        for (tick in listOf(0, 50, 100)) {
            val y = padTop + plotH - (tick / 100f) * plotH
            canvas.drawLine(padLeft, y, w - padRight, y, gridPaint)
            canvas.drawText(tick.toString(), 8f, y + 8f, textPaint)
        }

        if (points.isEmpty()) {
            canvas.drawText("No trend history yet — log bedside readings.", padLeft + 16f, padTop + plotH / 2f, textPaint)
            return
        }

        for ((_, col, extractor) in seriesMeta) {
            linePaint.color = col
            dotPaint.color = col
            val path = Path()
            points.forEachIndexed { idx, pt ->
                val x = if (points.size == 1) {
                    padLeft + plotW / 2f
                } else {
                    padLeft + (idx.toFloat() / (points.size - 1)) * plotW
                }
                val y = padTop + plotH - (extractor(pt) / 100f) * plotH
                if (idx == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            if (points.size > 1) {
                canvas.drawPath(path, linePaint)
            }
            points.forEachIndexed { idx, pt ->
                val x = if (points.size == 1) {
                    padLeft + plotW / 2f
                } else {
                    padLeft + (idx.toFloat() / (points.size - 1)) * plotW
                }
                val y = padTop + plotH - (extractor(pt) / 100f) * plotH
                canvas.drawCircle(x, y, 6.5f, dotPaint)
            }
        }

        // Bottom time axis labels
        canvas.drawText(points.first().label, padLeft, h - 10f, textPaint)
        if (points.size > 1) {
            val lastLabel = points.last().label
            val tw = textPaint.measureText(lastLabel)
            canvas.drawText(lastLabel, w - padRight - tw, h - 10f, textPaint)
        }
    }
}
