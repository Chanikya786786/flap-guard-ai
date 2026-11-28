package com.flapguard.ai

import android.content.Context
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Shader
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View
import androidx.core.content.ContextCompat
import java.util.Locale
import kotlin.math.max
import kotlin.math.min

class TrendChartView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    enum class MetricMode { SPO2, TEMP_DELTA, DOPPLER, RISK }

    private var points: List<ReadingPoint> = emptyList()
    private var mode: MetricMode = MetricMode.SPO2

    private fun dp(v: Float): Float = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v, resources.displayMetrics
    )

    private fun sp(v: Float): Float = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_SP, v, resources.displayMetrics
    )

    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        strokeWidth = dp(1f)
    }
    private val thresholdPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(1.25f)
        pathEffect = DashPathEffect(floatArrayOf(dp(5f), dp(4f)), 0f)
    }
    private val bandPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = sp(10.5f)
    }
    private val badgeTextPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = sp(10.5f)
        isFakeBoldText = true
    }
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(2.6f)
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(2f)
    }

    fun setReadings(readings: List<ReadingPoint>) {
        points = readings
        invalidate()
    }

    fun setMetricMode(newMode: MetricMode) {
        if (mode != newMode) {
            mode = newMode
            invalidate()
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return

        val colGrid = ContextCompat.getColor(context, R.color.fg_chart_grid)
        val colMuted = ContextCompat.getColor(context, R.color.fg_text_muted)
        val colSurface = ContextCompat.getColor(context, R.color.fg_surface)
        val colSafeBand = ContextCompat.getColor(context, R.color.fg_chart_safe_band)
        val colDangerBand = ContextCompat.getColor(context, R.color.fg_chart_danger_band)
        val colDanger = ContextCompat.getColor(context, R.color.fg_danger)

        gridPaint.color = colGrid
        textPaint.color = colMuted
        ringPaint.color = colSurface
        thresholdPaint.color = colDanger

        val padLeft = dp(44f)
        val padRight = dp(16f)
        val padTop = dp(24f)
        val padBottom = dp(26f)
        val plotW = w - padLeft - padRight
        val plotH = h - padTop - padBottom
        if (plotW <= 0f || plotH <= 0f) return

        val values: List<Double> = when (mode) {
            MetricMode.SPO2 -> points.map { it.spo2Pct }
            MetricMode.TEMP_DELTA -> points.map { it.tempDeltaC }
            MetricMode.DOPPLER -> points.map { it.dopplerCmS }
            MetricMode.RISK -> points.map { it.riskScorePct }
        }

        val maxObserved = values.maxOrNull() ?: 0.0
        val minObserved = values.minOrNull() ?: 0.0

        val minY: Double
        val maxY: Double
        val ticks: List<Double>
        val thresholdY: Double
        val safeRange: Pair<Double, Double>
        val dangerRange: Pair<Double, Double>
        val lineColor: Int
        val valueFormatter: (Double) -> String
        val tickFormatter: (Double) -> String

        when (mode) {
            MetricMode.SPO2 -> {
                minY = if (points.isNotEmpty() && minObserved < 50.0) max(0.0, (minObserved - 10.0)) else 50.0
                maxY = 100.0
                ticks = listOf(minY, (minY + 90.0) / 2.0, 90.0, 100.0)
                thresholdY = 90.0
                safeRange = 92.0 to 100.0
                dangerRange = minY to 90.0
                lineColor = ContextCompat.getColor(context, R.color.fg_info)
                valueFormatter = { v -> String.format(Locale.US, "%.1f%%", v) }
                tickFormatter = { v -> String.format(Locale.US, "%.0f%%", v) }
            }
            MetricMode.TEMP_DELTA -> {
                minY = 0.0
                maxY = max(4.0, maxObserved * 1.15)
                ticks = listOf(0.0, 1.8, maxY)
                thresholdY = 1.8
                safeRange = 0.0 to 1.5
                dangerRange = 1.8 to maxY
                lineColor = ContextCompat.getColor(context, R.color.fg_warning)
                valueFormatter = { v -> String.format(Locale.US, "%.1f°C", v) }
                tickFormatter = { v -> String.format(Locale.US, "%.1f°", v) }
            }
            MetricMode.DOPPLER -> {
                minY = 0.0
                maxY = max(30.0, maxObserved * 1.15)
                ticks = listOf(0.0, 12.0, maxY)
                thresholdY = 12.0
                safeRange = 15.0 to maxY
                dangerRange = 0.0 to 12.0
                lineColor = ContextCompat.getColor(context, R.color.fg_accent)
                valueFormatter = { v -> String.format(Locale.US, "%.1f", v) }
                tickFormatter = { v -> String.format(Locale.US, "%.0f", v) }
            }
            MetricMode.RISK -> {
                minY = 0.0
                maxY = 100.0
                ticks = listOf(0.0, 35.0, 70.0, 100.0)
                thresholdY = 70.0
                safeRange = 0.0 to 30.0
                dangerRange = 70.0 to 100.0
                lineColor = colDanger
                valueFormatter = { v -> String.format(Locale.US, "%.1f%%", v) }
                tickFormatter = { v -> String.format(Locale.US, "%.0f%%", v) }
            }
        }

        fun yFor(v: Double): Float {
            val clamped = max(minY, min(maxY, v))
            val frac = if (maxY > minY) ((clamped - minY) / (maxY - minY)).toFloat() else 0f
            return padTop + plotH - frac * plotH
        }

        // 1. Shaded safe & danger clinical threshold bands
        bandPaint.color = colSafeBand
        canvas.drawRect(padLeft, yFor(safeRange.second), w - padRight, yFor(safeRange.first), bandPaint)
        bandPaint.color = colDangerBand
        canvas.drawRect(padLeft, yFor(dangerRange.second), w - padRight, yFor(dangerRange.first), bandPaint)

        // 2. Horizontal grid lines & Y-axis labels
        for (tick in ticks) {
            val y = yFor(tick)
            canvas.drawLine(padLeft, y, w - padRight, y, gridPaint)
            val lbl = tickFormatter(tick)
            val tw = textPaint.measureText(lbl)
            canvas.drawText(lbl, padLeft - tw - dp(6f), y + dp(3.5f), textPaint)
        }

        // 3. Dashed critical threshold line
        val thY = yFor(thresholdY)
        canvas.drawLine(padLeft, thY, w - padRight, thY, thresholdPaint)

        if (points.isEmpty()) {
            val emptyMsg = "No assessments recorded yet"
            val tw = textPaint.measureText(emptyMsg)
            canvas.drawText(emptyMsg, padLeft + (plotW - tw) / 2f, padTop + plotH / 2f, textPaint)
            return
        }

        // 4. Plot line + soft vertical gradient area fill
        linePaint.color = lineColor
        dotPaint.color = lineColor
        badgeTextPaint.color = lineColor

        val linePath = Path()
        val fillPath = Path()
        val xs = FloatArray(points.size)
        val ys = FloatArray(points.size)

        for (i in points.indices) {
            val x = if (points.size == 1) {
                padLeft + plotW / 2f
            } else {
                padLeft + (i.toFloat() / (points.size - 1)) * plotW
            }
            val y = yFor(values[i])
            xs[i] = x
            ys[i] = y
            if (i == 0) {
                linePath.moveTo(x, y)
                fillPath.moveTo(x, padTop + plotH)
                fillPath.lineTo(x, y)
            } else {
                linePath.lineTo(x, y)
                fillPath.lineTo(x, y)
            }
        }

        if (points.size > 1) {
            fillPath.lineTo(xs.last(), padTop + plotH)
            fillPath.close()
            val topAlpha = (lineColor and 0x00FFFFFF) or 0x33000000
            val botAlpha = (lineColor and 0x00FFFFFF) or 0x03000000
            fillPaint.shader = LinearGradient(
                0f, padTop, 0f, padTop + plotH,
                topAlpha, botAlpha, Shader.TileMode.CLAMP
            )
            canvas.drawPath(fillPath, fillPaint)
            canvas.drawPath(linePath, linePaint)
        }

        // 5. Data point markers (skip interior dots if > 16 points to prevent clutter)
        val step = if (points.size > 16) max(1, points.size / 12) else 1
        for (i in points.indices) {
            val isEdge = (i == 0 || i == points.lastIndex)
            if (!isEdge && i % step != 0) continue
            val r = if (i == points.lastIndex) dp(4.8f) else dp(3.2f)
            canvas.drawCircle(xs[i], ys[i], r, dotPaint)
            canvas.drawCircle(xs[i], ys[i], r, ringPaint)
        }

        // 6. Latest value readout pill in top-right of chart
        val latestTxt = "Latest: ${valueFormatter(values.last())}"
        val ltw = badgeTextPaint.measureText(latestTxt)
        canvas.drawText(latestTxt, w - padRight - ltw, dp(14f), badgeTextPaint)

        // 7. Clean chronological X-axis timestamps (Start, Mid, Latest)
        val firstLbl = points.first().label
        canvas.drawText(firstLbl, padLeft, h - dp(6f), textPaint)
        if (points.size > 2) {
            val midIdx = points.size / 2
            val midLbl = points[midIdx].label
            val mtw = textPaint.measureText(midLbl)
            canvas.drawText(midLbl, padLeft + (plotW - mtw) / 2f, h - dp(6f), textPaint)
        }
        if (points.size > 1) {
            val lastLbl = points.last().label
            val tw = textPaint.measureText(lastLbl)
            canvas.drawText(lastLbl, w - padRight - tw, h - dp(6f), textPaint)
        }
    }
}
