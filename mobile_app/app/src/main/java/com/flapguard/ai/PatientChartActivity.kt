package com.flapguard.ai

import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import org.json.JSONArray
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.Executors

class PatientChartActivity : AppCompatActivity() {

    private val executor = Executors.newSingleThreadExecutor()
    private var wasBackgroundedForLock = false
    private var suppressChildLock = false
    private val mainHandler = Handler(Looper.getMainLooper())

    private lateinit var chartView: TrendChartView
    private lateinit var tvThresholdHint: TextView
    private lateinit var tabSpo2: TextView
    private lateinit var tabDelta: TextView
    private lateinit var tabDoppler: TextView
    private lateinit var tabRisk: TextView
    private lateinit var llRecentReadings: LinearLayout
    private lateinit var tvRecentEmpty: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        SecurePrefs.applySavedTheme(this)
        super.onCreate(savedInstanceState)
        if (intent.getBooleanExtra("flag_secure", false)) {
            window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
        setContentView(R.layout.activity_patient_chart)

        val patientId = intent.getIntExtra("patient_id", -1)
        val mrn = intent.getStringExtra("mrn").orEmpty()
        val fullName = intent.getStringExtra("full_name").orEmpty()
        val age = intent.getIntExtra("age", 0)
        val flapType = intent.getStringExtra("flap_type").orEmpty()
        val surgerySite = intent.getStringExtra("surgery_site").orEmpty()
        val hoursPostOp = intent.getDoubleExtra("hours_post_op", 0.0)
        val riskTier = intent.getStringExtra("risk_tier").orEmpty()
        val alertMessage = intent.getStringExtra("alert_message").orEmpty()
        val trendStatus = intent.getStringExtra("trend_status").orEmpty().ifBlank { "Baseline Established" }
        val confidenceLabel = intent.getStringExtra("confidence_label").orEmpty().ifBlank { "High" }
        val oodFlag = intent.getBooleanExtra("ood_flag", false)
        val schedLabel = intent.getStringExtra("check_schedule_label").orEmpty()
        val schedOverdue = intent.getBooleanExtra("check_overdue", false)
        val bedsideExam = intent.getStringExtra("bedside_exam_summary").orEmpty()
        val serverUrl = intent.getStringExtra("server_url").orEmpty()
        val authToken = intent.getStringExtra("auth_token").orEmpty()

        findViewById<ImageButton>(R.id.btn_chart_back).setOnClickListener { finish() }

        findViewById<TextView>(R.id.tv_sheet_patient_name).text = fullName
        findViewById<TextView>(R.id.tv_sheet_patient_meta).text = String.format(
            Locale.US, "%s  •  %dy  •  %.1fh post-op", mrn, age, hoursPostOp
        )
        findViewById<TextView>(R.id.tv_sheet_flap_info).text = "$flapType — $surgerySite"

        // Risk Badge
        val tvBadge = findViewById<TextView>(R.id.tv_sheet_risk_badge)
        tvBadge.text = if (riskTier == "Low") "Healthy" else riskTier.ifBlank { "New" }
        val (badgeBg, badgeFg) = tierColors(riskTier)
        tvBadge.backgroundTintList = ColorStateList.valueOf(badgeBg)
        tvBadge.setTextColor(badgeFg)

        // Schedule Pill
        val tvSched = findViewById<TextView>(R.id.tv_detail_schedule)
        if (schedLabel.isNotBlank()) {
            tvSched.visibility = View.VISIBLE
            tvSched.text = "Protocol: $schedLabel"
            tvSched.backgroundTintList = ColorStateList.valueOf(
                ContextCompat.getColor(this, if (schedOverdue) R.color.fg_danger_bg else R.color.fg_accent_soft)
            )
            tvSched.setTextColor(
                ContextCompat.getColor(this, if (schedOverdue) R.color.fg_danger else R.color.fg_accent)
            )
        }

        findViewById<TextView>(R.id.tv_detail_trend_badge).text = "Trend: $trendStatus"
        findViewById<TextView>(R.id.tv_detail_conf_badge).text = "Reliability: $confidenceLabel"
        findViewById<TextView>(R.id.tv_detail_ood_warning).visibility = if (oodFlag) View.VISIBLE else View.GONE

        // Latest Vitals Tiles
        val colNormal = ContextCompat.getColor(this, R.color.fg_text_primary)
        val colDanger = ContextCompat.getColor(this, R.color.fg_danger)

        val tvDelta = findViewById<TextView>(R.id.tv_detail_delta)
        if (intent.getBooleanExtra("has_delta", false)) {
            val v = intent.getDoubleExtra("temp_delta_c", 0.0)
            tvDelta.text = String.format(Locale.US, "%.2f °C", v)
            tvDelta.setTextColor(if (v >= 1.8) colDanger else colNormal)
        }

        val tvSpo2 = findViewById<TextView>(R.id.tv_detail_spo2)
        if (intent.getBooleanExtra("has_spo2", false)) {
            val v = intent.getDoubleExtra("spo2_pct", 0.0)
            tvSpo2.text = String.format(Locale.US, "%.1f%%", v)
            tvSpo2.setTextColor(if (v < 90.0) colDanger else colNormal)
        }

        val tvDoppler = findViewById<TextView>(R.id.tv_detail_doppler)
        if (intent.getBooleanExtra("has_doppler", false)) {
            val v = intent.getDoubleExtra("doppler_cm_s", 0.0)
            tvDoppler.text = String.format(Locale.US, "%.1f cm/s", v)
            tvDoppler.setTextColor(if (v < 12.0) colDanger else colNormal)
        }

        val tvVisual = findViewById<TextView>(R.id.tv_detail_visual)
        if (intent.getBooleanExtra("has_visual", false)) {
            val v = intent.getDoubleExtra("visual_score", 0.0)
            tvVisual.text = String.format(Locale.US, "%.0f / 100", v)
        }

        // Clinical Guidance (strip raw engineering URGENT [Critical]: prefixes)
        val cleanGuidance = alertMessage
            .replace(Regex("""^(URGENT|WARNING|WATCH|MONITOR)\s*\[[^\]]+\]:\s*""", RegexOption.IGNORE_CASE), "")
            .trim()
            .ifBlank { "No sensor assessments logged yet for this patient." }
        findViewById<TextView>(R.id.tv_detail_summary).text = cleanGuidance
        val guidanceCard = findViewById<MaterialCardView>(R.id.card_chart_guidance)
        if (riskTier == "Critical") {
            guidanceCard.setCardBackgroundColor(ContextCompat.getColor(this, R.color.fg_danger_bg))
            guidanceCard.strokeColor = ContextCompat.getColor(this, R.color.fg_danger_border)
        }

        // Structured Bedside Exam Rows
        if (bedsideExam.isNotBlank()) {
            findViewById<MaterialCardView>(R.id.card_chart_exam).visibility = View.VISIBLE
            val llExam = findViewById<LinearLayout>(R.id.ll_chart_exam_rows)
            val parts = bedsideExam.split(" · ").filter { it.isNotBlank() }
            for (part in parts) {
                val kv = part.split(":", limit = 2)
                val row = LinearLayout(this).apply {
                    orientation = LinearLayout.HORIZONTAL
                    setPadding(0, dp(4), 0, dp(4))
                }
                val tvKey = TextView(this).apply {
                    layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 0.42f)
                    text = kv.getOrNull(0)?.trim().orEmpty()
                    textSize = 12.5f
                    setTextColor(ContextCompat.getColor(this@PatientChartActivity, R.color.fg_text_muted))
                }
                val valStr = kv.getOrNull(1)?.trim().orEmpty()
                val abnormal = valStr.contains("Venous", true) || valStr.contains("Arterial", true) ||
                    valStr.contains("Absent", true) || valStr.contains("Tense", true) ||
                    valStr.contains("Weak", true) || valStr.contains("Flaccid", true)
                val tvVal = TextView(this).apply {
                    layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 0.58f)
                    text = valStr
                    textSize = 12.5f
                    setTextColor(
                        ContextCompat.getColor(
                            this@PatientChartActivity,
                            if (abnormal) R.color.fg_danger else R.color.fg_text_primary
                        )
                    )
                }
                row.addView(tvKey)
                row.addView(tvVal)
                llExam.addView(row)
            }
        }

        // Interactive Native-Scale Trend Chart Tabs
        chartView = findViewById(R.id.view_trend_chart)
        tvThresholdHint = findViewById(R.id.tv_chart_threshold_hint)
        tabSpo2 = findViewById(R.id.tab_metric_spo2)
        tabDelta = findViewById(R.id.tab_metric_delta)
        tabDoppler = findViewById(R.id.tab_metric_doppler)
        tabRisk = findViewById(R.id.tab_metric_risk)
        llRecentReadings = findViewById(R.id.ll_recent_readings)
        tvRecentEmpty = findViewById(R.id.tv_recent_empty)

        tabSpo2.setOnClickListener { selectMetricTab(TrendChartView.MetricMode.SPO2) }
        tabDelta.setOnClickListener { selectMetricTab(TrendChartView.MetricMode.TEMP_DELTA) }
        tabDoppler.setOnClickListener { selectMetricTab(TrendChartView.MetricMode.DOPPLER) }
        tabRisk.setOnClickListener { selectMetricTab(TrendChartView.MetricMode.RISK) }
        selectMetricTab(TrendChartView.MetricMode.SPO2)

        // Sticky Footer Actions
        findViewById<MaterialButton>(R.id.btn_sheet_assess).setOnClickListener {
            returnAction("assess", patientId)
        }
        findViewById<MaterialButton>(R.id.btn_chart_outcome).setOnClickListener {
            returnAction("outcome", patientId)
        }
        findViewById<MaterialButton>(R.id.btn_sheet_remove).setOnClickListener {
            returnAction("archive", patientId)
        }

        if (patientId > 0 && serverUrl.isNotBlank()) {
            loadReadings(serverUrl, authToken, patientId)
        } else {
            tvRecentEmpty.text = "No trend history available."
        }
    }

    private fun selectMetricTab(mode: TrendChartView.MetricMode) {
        chartView.setMetricMode(mode)
        tvThresholdHint.text = when (mode) {
            TrendChartView.MetricMode.SPO2 -> "Alert < 90%"
            TrendChartView.MetricMode.TEMP_DELTA -> "Alert ≥ 1.8°C"
            TrendChartView.MetricMode.DOPPLER -> "Alert < 12 cm/s"
            TrendChartView.MetricMode.RISK -> "Critical ≥ 70%"
        }
        val activeBg = ContextCompat.getColor(this, R.color.fg_primary)
        val activeFg = ContextCompat.getColor(this, R.color.white)
        val idleBg = ContextCompat.getColor(this, R.color.fg_surface_soft)
        val idleFg = ContextCompat.getColor(this, R.color.fg_text_secondary)

        val tabs = listOf(
            tabSpo2 to TrendChartView.MetricMode.SPO2,
            tabDelta to TrendChartView.MetricMode.TEMP_DELTA,
            tabDoppler to TrendChartView.MetricMode.DOPPLER,
            tabRisk to TrendChartView.MetricMode.RISK
        )
        for ((tv, m) in tabs) {
            val active = (m == mode)
            tv.backgroundTintList = ColorStateList.valueOf(if (active) activeBg else idleBg)
            tv.setTextColor(if (active) activeFg else idleFg)
        }
    }

    private fun loadReadings(serverUrl: String, authToken: String, patientId: Int) {
        executor.execute {
            val res = ApiClient.request(serverUrl, "/mobile/patients/$patientId/readings", "GET", null, authToken)
            val pts = mutableListOf<ReadingPoint>()
            if (res.ok) {
                val arr = res.json.optJSONArray("readings") ?: JSONArray()
                // API returns newest-first -> reverse for true chronological left-to-right trajectory
                for (i in arr.length() - 1 downTo 0) {
                    val r = arr.getJSONObject(i)
                    val hrs = r.optDouble("hours_post_op", 0.0)
                    val timeLbl = formatLocalTime(r.optString("created_at", ""))
                        ?: String.format(Locale.US, "%.0fh", hrs)
                    pts.add(
                        ReadingPoint(
                            label = timeLbl,
                            tempDeltaC = r.optDouble("temp_delta_c", 0.0),
                            spo2Pct = r.optDouble("spo2_pct", 0.0),
                            dopplerCmS = r.optDouble("doppler_flow_cm_s", 0.0),
                            riskScorePct = r.optDouble("risk_score_pct", 0.0),
                            riskTier = r.optString("risk_tier", ""),
                            predictedClass = r.optString("predicted_class", ""),
                            bedsideExam = r.optString("bedside_exam_summary", ""),
                            oodFlag = r.optBoolean("ood_flag", false)
                        )
                    )
                }
            }
            mainHandler.post {
                chartView.setReadings(pts)
                renderRecentLog(pts.asReversed().take(6))
            }
        }
    }

    private fun renderRecentLog(recent: List<ReadingPoint>) {
        llRecentReadings.removeAllViews()
        if (recent.isEmpty()) {
            tvRecentEmpty.visibility = View.VISIBLE
            tvRecentEmpty.text = "No assessments recorded yet."
            return
        }
        tvRecentEmpty.visibility = View.GONE
        for ((idx, pt) in recent.withIndex()) {
            if (idx > 0) {
                val div = View(this).apply {
                    layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(1))
                    setBackgroundColor(ContextCompat.getColor(this@PatientChartActivity, R.color.fg_divider))
                }
                llRecentReadings.addView(div)
            }
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(0, dp(9), 0, dp(9))
            }
            val top = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val tvTime = TextView(this).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                text = "${pt.label}  •  ${pt.predictedClass} (${String.format(Locale.US, "%.1f%%", pt.riskScorePct)})"
                textSize = 12.5f
                setTextColor(ContextCompat.getColor(this@PatientChartActivity, R.color.fg_text_primary))
            }
            val (bg, fg) = tierColors(pt.riskTier)
            val tvPill = TextView(this).apply {
                text = if (pt.riskTier == "Low") "Healthy" else pt.riskTier
                textSize = 10.5f
                setPadding(dp(8), dp(2), dp(8), dp(2))
                background = ContextCompat.getDrawable(this@PatientChartActivity, R.drawable.bg_badge)
                backgroundTintList = ColorStateList.valueOf(bg)
                setTextColor(fg)
            }
            top.addView(tvTime)
            top.addView(tvPill)
            row.addView(top)

            val tvMetrics = TextView(this).apply {
                text = String.format(
                    Locale.US, "ΔT %.1f°C  •  SpO₂ %.1f%%  •  Doppler %.1f cm/s",
                    pt.tempDeltaC, pt.spo2Pct, pt.dopplerCmS
                )
                textSize = 11.5f
                setPadding(0, dp(3), 0, 0)
                setTextColor(ContextCompat.getColor(this@PatientChartActivity, R.color.fg_text_muted))
            }
            row.addView(tvMetrics)

            if (pt.bedsideExam.isNotBlank()) {
                val tvExam = TextView(this).apply {
                    text = pt.bedsideExam
                    textSize = 11f
                    setPadding(0, dp(2), 0, 0)
                    setTextColor(ContextCompat.getColor(this@PatientChartActivity, R.color.fg_text_secondary))
                }
                row.addView(tvExam)
            }
            llRecentReadings.addView(row)
        }
    }

    private fun formatLocalTime(iso: String): String? {
        if (iso.isBlank()) return null
        return try {
            val clean = iso.substringBefore(".").substringBefore("+").substringBefore("Z")
            val parser = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply {
                timeZone = TimeZone.getTimeZone("UTC")
            }
            val d = parser.parse(clean) ?: return null
            SimpleDateFormat("HH:mm", Locale.getDefault()).format(d)
        } catch (_: Exception) {
            null
        }
    }

    private fun tierColors(tier: String): Pair<Int, Int> {
        return when (tier) {
            "Critical" -> ContextCompat.getColor(this, R.color.fg_danger_bg) to ContextCompat.getColor(this, R.color.fg_danger)
            "High" -> ContextCompat.getColor(this, R.color.fg_warning_bg) to ContextCompat.getColor(this, R.color.fg_warning)
            "Moderate" -> ContextCompat.getColor(this, R.color.fg_info_bg) to ContextCompat.getColor(this, R.color.fg_info)
            "Low" -> ContextCompat.getColor(this, R.color.fg_success_bg) to ContextCompat.getColor(this, R.color.fg_success)
            else -> ContextCompat.getColor(this, R.color.fg_surface_soft) to ContextCompat.getColor(this, R.color.fg_text_muted)
        }
    }

    private fun returnAction(action: String, patientId: Int) {
        setResult(
            RESULT_OK,
            Intent().putExtra("action", action).putExtra("patient_id", patientId)
        )
        finish()
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()


    override fun onStop() {
        super.onStop()
        if (!isChangingConfigurations && !isFinishing && !suppressChildLock && SecurePrefs.isAppLockEnabled(this)) {
            wasBackgroundedForLock = true
        }
    }

    override fun onResume() {
        super.onResume()
        suppressChildLock = false
        if (wasBackgroundedForLock && SecurePrefs.isAppLockEnabled(this)) {
            wasBackgroundedForLock = false
            finish()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        executor.shutdownNow()
    }
}
