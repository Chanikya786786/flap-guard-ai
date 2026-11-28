package com.flapguard.ai

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.WindowManager
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import org.json.JSONArray
import java.util.concurrent.Executors

class SbarHandoffActivity : AppCompatActivity() {

    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    private lateinit var tvMeta: TextView
    private lateinit var tvKpiTotal: TextView
    private lateinit var tvKpiCrit: TextView
    private lateinit var tvKpiOverdue: TextView
    private lateinit var tvKpiAlerts: TextView
    private lateinit var tvEmpty: TextView
    private lateinit var llCards: LinearLayout
    private lateinit var btnRefresh: ImageButton
    private lateinit var progressBar: ProgressBar

    private var serverUrl: String = ""
    private var authToken: String = ""
    private var latestSbarText: String = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (intent.getBooleanExtra("flag_secure", false)) {
            window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
        setContentView(R.layout.activity_sbar_handoff)

        serverUrl = intent.getStringExtra("server_url").orEmpty()
        authToken = intent.getStringExtra("auth_token").orEmpty()

        tvMeta = findViewById(R.id.tv_sbar_meta)
        tvKpiTotal = findViewById(R.id.tv_sbar_kpi_total)
        tvKpiCrit = findViewById(R.id.tv_sbar_kpi_crit)
        tvKpiOverdue = findViewById(R.id.tv_sbar_kpi_overdue)
        tvKpiAlerts = findViewById(R.id.tv_sbar_kpi_alerts)
        tvEmpty = findViewById(R.id.tv_sbar_empty)
        llCards = findViewById(R.id.ll_sbar_cards)
        btnRefresh = findViewById(R.id.btn_sbar_refresh)
        progressBar = findViewById(R.id.progress_sbar)

        findViewById<ImageButton>(R.id.btn_sbar_back).setOnClickListener { finish() }
        btnRefresh.setOnClickListener { fetchHandoff() }

        findViewById<MaterialButton>(R.id.btn_sbar_copy).setOnClickListener {
            if (latestSbarText.isBlank()) {
                Toast.makeText(this, "Handoff report still loading.", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("FlapGuard SBAR Handoff", latestSbarText))
            Toast.makeText(this, "SBAR handoff copied to clipboard.", Toast.LENGTH_SHORT).show()
        }

        findViewById<MaterialButton>(R.id.btn_sbar_share).setOnClickListener {
            if (latestSbarText.isBlank()) {
                Toast.makeText(this, "Handoff report still loading.", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val share = Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_SUBJECT, "FlapGuard AI — Shift Handoff Report (SBAR)")
                putExtra(Intent.EXTRA_TEXT, latestSbarText)
            }
            startActivity(Intent.createChooser(share, "Share Shift Handoff"))
        }

        fetchHandoff()
    }

    private fun fetchHandoff() {
        btnRefresh.visibility = View.INVISIBLE
        progressBar.visibility = View.VISIBLE
        executor.execute {
            val res = ApiClient.request(serverUrl, "/mobile/handoff", "GET", null, authToken)
            mainHandler.post {
                btnRefresh.visibility = View.VISIBLE
                progressBar.visibility = View.GONE
                if (!res.ok) {
                    tvMeta.text = "Unable to sync handoff"
                    tvEmpty.visibility = View.VISIBLE
                    tvEmpty.text = res.errorMessage
                    return@post
                }
                val h = res.json.optJSONObject("handoff") ?: res.json
                latestSbarText = h.optString("sbar_text", "")
                val genAt = h.optString("generated_at_utc", "")
                val genBy = h.optString("generated_by", "")
                tvMeta.text = if (genBy.isNotBlank()) "$genAt  •  $genBy" else genAt

                tvKpiTotal.text = h.optInt("total_patients", 0).toString()
                tvKpiCrit.text = h.optInt("critical_high_count", 0).toString()
                tvKpiOverdue.text = h.optInt("overdue_count", 0).toString()
                tvKpiAlerts.text = h.optInt("active_alerts_count", 0).toString()

                val patients = h.optJSONArray("patients") ?: JSONArray()
                renderCards(patients)
            }
        }
    }

    private fun renderCards(patients: JSONArray) {
        llCards.removeAllViews()
        if (patients.length() == 0) {
            tvEmpty.visibility = View.VISIBLE
            tvEmpty.text = "No active postoperative flap patients enrolled."
            return
        }
        tvEmpty.visibility = View.GONE
        val inflater = LayoutInflater.from(this)
        val density = resources.displayMetrics.density

        for (i in 0 until patients.length()) {
            val p = patients.getJSONObject(i)
            val cardView = inflater.inflate(R.layout.item_sbar_card, llCards, false)
            val card = cardView.findViewById<MaterialCardView>(R.id.card_sbar_patient)
            val tier = p.optString("tier", "Unassessed")
            val schedObj = p.optJSONObject("schedule")
            val schedLbl = schedObj?.optString("status_label").orEmpty()
            val overdue = schedObj?.optBoolean("overdue", false) ?: false
            val pid = p.optInt("patient_id", -1)

            cardView.findViewById<TextView>(R.id.tv_sbar_rank).text = (i + 1).toString()
            cardView.findViewById<TextView>(R.id.tv_sbar_name).text = p.optString("patient_name")
            cardView.findViewById<TextView>(R.id.tv_sbar_mrn).text = p.optString("mrn")

            val tvTier = cardView.findViewById<TextView>(R.id.tv_sbar_tier)
            tvTier.text = if (tier == "Low") "Healthy" else tier
            val (tBg, tFg) = when (tier) {
                "Critical" -> ContextCompat.getColor(this, R.color.fg_danger_bg) to ContextCompat.getColor(this, R.color.fg_danger)
                "High" -> ContextCompat.getColor(this, R.color.fg_warning_bg) to ContextCompat.getColor(this, R.color.fg_warning)
                "Moderate" -> ContextCompat.getColor(this, R.color.fg_info_bg) to ContextCompat.getColor(this, R.color.fg_info)
                "Low" -> ContextCompat.getColor(this, R.color.fg_success_bg) to ContextCompat.getColor(this, R.color.fg_success)
                else -> ContextCompat.getColor(this, R.color.fg_surface_soft) to ContextCompat.getColor(this, R.color.fg_text_muted)
            }
            tvTier.backgroundTintList = ColorStateList.valueOf(tBg)
            tvTier.setTextColor(tFg)

            if (tier == "Critical") {
                card.strokeColor = ContextCompat.getColor(this, R.color.fg_danger_border)
                card.strokeWidth = (1.5f * density).toInt()
            }

            val tvSched = cardView.findViewById<TextView>(R.id.tv_sbar_sched)
            if (schedLbl.isNotBlank()) {
                tvSched.visibility = View.VISIBLE
                tvSched.text = schedLbl
                tvSched.backgroundTintList = ColorStateList.valueOf(
                    ContextCompat.getColor(this, if (overdue) R.color.fg_danger_bg else R.color.fg_accent_soft)
                )
                tvSched.setTextColor(
                    ContextCompat.getColor(this, if (overdue) R.color.fg_danger else R.color.fg_accent)
                )
            } else {
                tvSched.visibility = View.GONE
            }

            cardView.findViewById<TextView>(R.id.tv_sbar_s).text = p.optString("situation")
            cardView.findViewById<TextView>(R.id.tv_sbar_b).text = p.optString("background")
            cardView.findViewById<TextView>(R.id.tv_sbar_a).text = p.optString("assessment")
            cardView.findViewById<TextView>(R.id.tv_sbar_r).text = p.optString("recommendation")

            val boxR = cardView.findViewById<LinearLayout>(R.id.box_sbar_r)
            val badgeR = cardView.findViewById<TextView>(R.id.tv_sbar_r_badge)
            val (rBg, rFg) = when {
                tier == "Critical" -> ContextCompat.getColor(this, R.color.fg_danger_bg) to ContextCompat.getColor(this, R.color.fg_danger)
                tier == "High" || overdue -> ContextCompat.getColor(this, R.color.fg_warning_bg) to ContextCompat.getColor(this, R.color.fg_warning)
                else -> ContextCompat.getColor(this, R.color.fg_success_bg) to ContextCompat.getColor(this, R.color.fg_success)
            }
            boxR.backgroundTintList = ColorStateList.valueOf(rBg)
            badgeR.backgroundTintList = ColorStateList.valueOf(rFg)
            badgeR.setTextColor(ContextCompat.getColor(this, R.color.white))

            card.setOnClickListener {
                if (pid > 0) {
                    setResult(RESULT_OK, Intent().putExtra("open_patient_id", pid))
                    finish()
                }
            }
            llCards.addView(cardView)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        executor.shutdownNow()
    }
}
