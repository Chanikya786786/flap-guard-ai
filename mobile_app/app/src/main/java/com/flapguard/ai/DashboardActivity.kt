package com.flapguard.ai

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors

class DashboardActivity : AppCompatActivity() {

    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    private lateinit var serverUrl: String
    private lateinit var authToken: String

    private val patientIds = mutableListOf<Int>()
    private val patientLabels = mutableListOf<String>()
    private val notifiedAlertIds = mutableSetOf<Int>()

    private var scannedRedness = 0.14
    private var scannedCyanosis = 0.03
    private var scannedPallor = 0.42

    private lateinit var tvStatus: TextView
    private lateinit var tvLiveBanner: TextView
    private lateinit var patientsContainer: LinearLayout
    private lateinit var alertsContainer: LinearLayout
    private lateinit var spinnerPatient: Spinner
    private lateinit var spinnerOutcomePatient: Spinner
    private lateinit var ivFlapPreview: ImageView
    private lateinit var tvVisionResult: TextView
    private lateinit var tvPredictionResult: TextView
    private lateinit var tvOutcomeStatus: TextView

    private val pollRunnable = object : Runnable {
        override fun run() {
            fetchAlerts(silent = true)
            mainHandler.postDelayed(this, 5000L)
        }
    }

    private val cameraLauncher = registerForActivityResult(
        ActivityResultContracts.TakePicturePreview()
    ) { bitmap: Bitmap? ->
        if (bitmap != null) {
            ivFlapPreview.setImageBitmap(bitmap)
            ivFlapPreview.visibility = View.VISIBLE
            sendBitmapForAnalysis(bitmap)
        }
    }

    private val galleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri ->
        if (uri != null) {
            try {
                contentResolver.openInputStream(uri)?.use { stream ->
                    val bitmap = BitmapFactory.decodeStream(stream)
                    if (bitmap != null) {
                        ivFlapPreview.setImageBitmap(bitmap)
                        ivFlapPreview.visibility = View.VISIBLE
                        sendBitmapForAnalysis(bitmap)
                    }
                }
            } catch (e: Exception) {
                tvVisionResult.text = "Could not load image: ${e.message}"
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_dashboard)

        val prefs = getSharedPreferences("flapguard_prefs", Context.MODE_PRIVATE)
        serverUrl = prefs.getString("server_url", "http://127.0.0.1:5000") ?: "http://127.0.0.1:5000"
        authToken = prefs.getString("auth_token", "") ?: ""
        val userName = prefs.getString("user_name", "Surgeon") ?: "Surgeon"
        val userRole = prefs.getString("user_role", "Surgeon") ?: "Surgeon"

        if (authToken.isBlank()) {
            logoutToLogin()
            return
        }

        createNotificationChannel()
        requestNotificationPermissionIfNeeded()

        findViewById<TextView>(R.id.tv_logged_user).text = "$userName ($userRole)"
        tvStatus = findViewById(R.id.tv_dashboard_status)
        tvLiveBanner = findViewById(R.id.tv_live_alert_banner)
        patientsContainer = findViewById(R.id.patients_container)
        alertsContainer = findViewById(R.id.alerts_container)
        spinnerPatient = findViewById(R.id.spinner_patient)
        spinnerOutcomePatient = findViewById(R.id.spinner_outcome_patient)
        ivFlapPreview = findViewById(R.id.iv_flap_preview)
        tvVisionResult = findViewById(R.id.tv_vision_result)
        tvPredictionResult = findViewById(R.id.tv_prediction_result)
        tvOutcomeStatus = findViewById(R.id.tv_outcome_status)

        val sectionPatients = findViewById<LinearLayout>(R.id.section_patients)
        val sectionScanner = findViewById<LinearLayout>(R.id.section_scanner)
        val sectionAlerts = findViewById<LinearLayout>(R.id.section_alerts)
        val sectionOutcomes = findViewById<LinearLayout>(R.id.section_outcomes)

        val tabPatients = findViewById<Button>(R.id.tab_patients)
        val tabScanner = findViewById<Button>(R.id.tab_scanner)
        val tabAlerts = findViewById<Button>(R.id.tab_alerts)
        val tabOutcomes = findViewById<Button>(R.id.tab_outcomes)

        fun showSection(active: View, activeBtn: Button) {
            listOf(sectionPatients, sectionScanner, sectionAlerts, sectionOutcomes).forEach {
                it.visibility = if (it == active) View.VISIBLE else View.GONE
            }
            listOf(tabPatients, tabScanner, tabAlerts, tabOutcomes).forEach {
                it.setBackgroundColor(Color.parseColor(if (it == activeBtn) "#0F4C81" else "#475569"))
            }
        }

        tabPatients.setOnClickListener {
            showSection(sectionPatients, tabPatients)
            fetchPatients()
        }
        tabScanner.setOnClickListener { showSection(sectionScanner, tabScanner) }
        tabAlerts.setOnClickListener {
            showSection(sectionAlerts, tabAlerts)
            fetchAlerts(silent = false)
        }
        tabOutcomes.setOnClickListener { showSection(sectionOutcomes, tabOutcomes) }

        tvLiveBanner.setOnClickListener {
            showSection(sectionAlerts, tabAlerts)
            fetchAlerts(silent = false)
        }

        findViewById<Button>(R.id.btn_refresh_all).setOnClickListener {
            fetchPatients()
            fetchAlerts(silent = false)
        }
        findViewById<Button>(R.id.btn_logout).setOnClickListener { logoutToLogin() }

        // Camera & Gallery Scan
        findViewById<Button>(R.id.btn_open_camera).setOnClickListener { cameraLauncher.launch(null) }
        findViewById<Button>(R.id.btn_pick_gallery).setOnClickListener { galleryLauncher.launch("image/*") }

        // Submit Sensor Reading
        findViewById<Button>(R.id.btn_submit_reading).setOnClickListener { submitSensorReading() }

        // Live Simulator triggers
        findViewById<Button>(R.id.btn_sim_venous).setOnClickListener {
            triggerSimulation("venous_deteriorating", 7)
        }
        findViewById<Button>(R.id.btn_sim_arterial).setOnClickListener {
            triggerSimulation("arterial_deteriorating", 7)
        }

        // Outcome Spinner & Submit
        val outcomeSpinner = findViewById<Spinner>(R.id.spinner_outcome_label)
        outcomeSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            listOf("Normal", "Venous Compromise", "Arterial Compromise")
        )
        findViewById<Button>(R.id.btn_save_outcome).setOnClickListener { submitOutcome(outcomeSpinner) }

        fetchPatients()
        fetchAlerts(silent = false)
    }

    override fun onResume() {
        super.onResume()
        mainHandler.postDelayed(pollRunnable, 5000L)
    }

    override fun onPause() {
        super.onPause()
        mainHandler.removeCallbacks(pollRunnable)
    }

    private fun logoutToLogin() {
        getSharedPreferences("flapguard_prefs", Context.MODE_PRIVATE).edit()
            .remove("auth_token")
            .apply()
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    private fun fetchPatients() {
        tvStatus.text = "Syncing ward patients from $serverUrl ..."
        executor.execute {
            val res = ApiClient.request(serverUrl, "/mobile/patients", "GET", null, authToken)
            mainHandler.post {
                if (!res.ok) {
                    tvStatus.text = "Sync error: ${res.errorMessage}"
                    return@post
                }
                val arr = res.json.optJSONArray("patients") ?: JSONArray()
                renderPatients(arr)
                tvStatus.text = "Synced ${arr.length()} postoperative patients."
            }
        }
    }

    private fun renderPatients(arr: JSONArray) {
        patientsContainer.removeAllViews()
        patientIds.clear()
        patientLabels.clear()

        for (i in 0 until arr.length()) {
            val p = arr.getJSONObject(i)
            val id = p.getInt("id")
            val name = p.optString("full_name")
            val mrn = p.optString("mrn")
            val flap = p.optString("flap_type")
            val latest = p.optJSONObject("latest_reading")
            val tier = latest?.optString("risk_tier") ?: "Unassessed"
            val pred = latest?.optString("predicted_class") ?: "No reading yet"
            val riskPct = latest?.optDouble("risk_score_pct", 0.0) ?: 0.0
            val deltaT = latest?.optDouble("temp_delta_c", 0.0) ?: 0.0
            val spo2 = latest?.optDouble("spo2_pct", 0.0) ?: 0.0
            val doppler = latest?.optDouble("doppler_flow_cm_s", 0.0) ?: 0.0

            patientIds.add(id)
            patientLabels.add("$name ($mrn)")

            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(28, 24, 28, 24)
                setBackgroundColor(
                    when (tier) {
                        "Critical" -> Color.parseColor("#FEF2F2")
                        "High" -> Color.parseColor("#FFFBEB")
                        "Moderate" -> Color.parseColor("#EFF6FF")
                        else -> Color.parseColor("#FFFFFF")
                    }
                )
                val lp = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                )
                lp.bottomMargin = 20
                layoutParams = lp
            }

            val titleTv = TextView(this).apply {
                text = "[$tier] $name ($mrn)"
                textSize = 16f
                setTextColor(
                    if (tier == "Critical") Color.parseColor("#991B1B") else Color.parseColor("#0F172A")
                )
                setTypeface(null, android.graphics.Typeface.BOLD)
            }
            val metaTv = TextView(this).apply {
                text = "$flap\nAI: $pred ($riskPct% risk)\nDelta-T: ${deltaT}C | SpO2: ${spo2}% | Doppler: ${doppler} cm/s"
                textSize = 13f
                setTextColor(Color.parseColor("#334155"))
            }

            card.addView(titleTv)
            card.addView(metaTv)
            patientsContainer.addView(card)
        }

        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, patientLabels)
        spinnerPatient.adapter = adapter
        spinnerOutcomePatient.adapter = adapter
    }

    private fun sendBitmapForAnalysis(bitmap: Bitmap) {
        tvVisionResult.text = "Analyzing flap color locally via OpenCV..."
        executor.execute {
            val scaled = Bitmap.createScaledBitmap(bitmap, 320, 240, true)
            val baos = ByteArrayOutputStream()
            scaled.compress(Bitmap.CompressFormat.JPEG, 85, baos)
            val encoded = Base64.encodeToString(baos.toByteArray(), Base64.NO_WRAP)

            val body = JSONObject().put("image_base64", encoded)
            val res = ApiClient.request(serverUrl, "/mobile/analyze-image", "POST", body, authToken)

            mainHandler.post {
                if (res.ok) {
                    scannedRedness = res.json.optDouble("visual_redness_index", 0.14)
                    scannedCyanosis = res.json.optDouble("visual_cyanosis_index", 0.03)
                    scannedPallor = res.json.optDouble("visual_pallor_index", 0.42)
                    val score = res.json.optDouble("visual_perfusion_score", 0.0)
                    val triage = res.json.optString("visual_triage_label", "Unknown")

                    tvVisionResult.text =
                        "Visual Triage: $triage\nVisual Perfusion Score: $score / 100\n" +
                        "Redness: $scannedRedness | Cyanosis: $scannedCyanosis | Pallor: $scannedPallor"
                } else {
                    tvVisionResult.text = "Image analysis failed: ${res.errorMessage}"
                }
            }
        }
    }

    private fun selectedPatientId(spinner: Spinner): Int? {
        val idx = spinner.selectedItemPosition
        return if (idx in patientIds.indices) patientIds[idx] else null
    }

    private fun submitSensorReading() {
        val patientId = selectedPatientId(spinnerPatient)
        if (patientId == null) {
            tvPredictionResult.text = "No patient loaded yet. Tap Sync first."
            return
        }

        val irTemp = findViewById<EditText>(R.id.et_ir_temp).text.toString().toDoubleOrNull()
        val ctrlTemp = findViewById<EditText>(R.id.et_ctrl_temp).text.toString().toDoubleOrNull()
        val spo2 = findViewById<EditText>(R.id.et_spo2).text.toString().toDoubleOrNull()
        val doppler = findViewById<EditText>(R.id.et_doppler).text.toString().toDoubleOrNull()
        val hours = findViewById<EditText>(R.id.et_hours).text.toString().toDoubleOrNull() ?: 12.0

        if (irTemp == null || ctrlTemp == null || spo2 == null || doppler == null) {
            tvPredictionResult.text = "Please enter valid numeric sensor readings."
            return
        }

        tvPredictionResult.text = "Running local AI multisensor fusion..."
        executor.execute {
            val payload = JSONObject().apply {
                put("ir_flap_temp_c", irTemp)
                put("control_skin_temp_c", ctrlTemp)
                put("spo2_pct", spo2)
                put("doppler_flow_cm_s", doppler)
                put("hours_post_op", hours)
                put("visual_redness_index", scannedRedness)
                put("visual_cyanosis_index", scannedCyanosis)
                put("visual_pallor_index", scannedPallor)
            }
            val res = ApiClient.request(
                serverUrl, "/mobile/patients/$patientId/reading", "POST", payload, authToken
            )
            mainHandler.post {
                if (res.ok) {
                    val r = res.json.getJSONObject("reading")
                    val predClass = r.optString("predicted_class")
                    val riskTier = r.optString("risk_tier")
                    val riskPct = r.optDouble("risk_score_pct")
                    val msg = r.optString("alert_message")
                    tvPredictionResult.text = "Prediction: $predClass\nRisk Tier: $riskTier ($riskPct%)\n$msg"
                    fetchPatients()
                    fetchAlerts(silent = false)
                } else {
                    tvPredictionResult.text = "Error: ${res.errorMessage}"
                }
            }
        }
    }

    private fun triggerSimulation(scenario: String, step: Int) {
        val patientId = selectedPatientId(spinnerPatient) ?: return
        tvPredictionResult.text = "Triggering $scenario simulation..."
        executor.execute {
            val payload = JSONObject().put("scenario", scenario).put("step", step)
            val res = ApiClient.request(
                serverUrl, "/mobile/patients/$patientId/simulate", "POST", payload, authToken
            )
            mainHandler.post {
                if (res.ok) {
                    val r = res.json.getJSONObject("reading")
                    tvPredictionResult.text =
                        "Simulated: ${r.optString("predicted_class")} [${r.optString("risk_tier")}]\n" +
                        r.optString("alert_message")
                    fetchPatients()
                    fetchAlerts(silent = false)
                } else {
                    tvPredictionResult.text = "Simulation failed: ${res.errorMessage}"
                }
            }
        }
    }

    private fun fetchAlerts(silent: Boolean) {
        executor.execute {
            val res = ApiClient.request(serverUrl, "/mobile/alerts", "GET", null, authToken)
            mainHandler.post {
                if (!res.ok) {
                    if (!silent) tvStatus.text = "Alert sync error: ${res.errorMessage}"
                    return@post
                }
                val unack = res.json.optInt("unacknowledged_count", 0)
                val alerts = res.json.optJSONArray("alerts") ?: JSONArray()

                if (unack > 0) {
                    tvLiveBanner.visibility = View.VISIBLE
                    tvLiveBanner.text = "URGENT: $unack Unacknowledged Flap Failure Alert(s) - Tap to Inspect"
                } else {
                    tvLiveBanner.visibility = View.GONE
                }

                renderAlerts(alerts)
            }
        }
    }

    private fun renderAlerts(alerts: JSONArray) {
        alertsContainer.removeAllViews()
        if (alerts.length() == 0) {
            alertsContainer.addView(TextView(this).apply {
                text = "No compromise alerts recorded."
                setPadding(12, 12, 12, 12)
            })
            return
        }

        for (i in 0 until alerts.length()) {
            val a = alerts.getJSONObject(i)
            val id = a.getInt("id")
            val pName = a.optString("patient_name")
            val tier = a.optString("risk_tier")
            val msg = a.optString("alert_message")
            val acked = a.optBoolean("alert_acknowledged", false)

            if (!acked && notifiedAlertIds.add(id)) {
                pushSurgeonNotification(id, "$tier Flap Alert: $pName", msg)
            }

            val box = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(24, 20, 24, 20)
                setBackgroundColor(if (acked) Color.parseColor("#FFFFFF") else Color.parseColor("#FEF2F2"))
                val lp = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                )
                lp.bottomMargin = 16
                layoutParams = lp
            }

            box.addView(TextView(this).apply {
                text = "#$id | $pName [$tier]"
                textSize = 15f
                setTypeface(null, android.graphics.Typeface.BOLD)
                setTextColor(if (acked) Color.parseColor("#334155") else Color.parseColor("#991B1B"))
            })
            box.addView(TextView(this).apply {
                text = msg
                textSize = 13f
                setTextColor(Color.parseColor("#1E293B"))
            })

            if (!acked) {
                box.addView(Button(this).apply {
                    text = "Acknowledge Alert"
                    setBackgroundColor(Color.parseColor("#0F4C81"))
                    setTextColor(Color.WHITE)
                    setOnClickListener { acknowledgeAlert(id) }
                })
            } else {
                box.addView(TextView(this).apply {
                    text = "Acknowledged by ${a.optString("acknowledged_by", "Surgeon")}"
                    textSize = 12f
                    setTextColor(Color.parseColor("#15803D"))
                })
            }
            alertsContainer.addView(box)
        }
    }

    private fun acknowledgeAlert(readingId: Int) {
        executor.execute {
            val res = ApiClient.request(
                serverUrl, "/mobile/alerts/$readingId/acknowledge", "POST", JSONObject(), authToken
            )
            mainHandler.post {
                if (res.ok) {
                    tvStatus.text = "Alert #$readingId acknowledged."
                    fetchAlerts(silent = false)
                }
            }
        }
    }

    private fun submitOutcome(outcomeSpinner: Spinner) {
        val patientId = selectedPatientId(spinnerOutcomePatient) ?: return
        val outcome = outcomeSpinner.selectedItem.toString()
        val notes = findViewById<EditText>(R.id.et_outcome_notes).text.toString().trim()

        tvOutcomeStatus.text = "Saving postoperative outcome..."
        executor.execute {
            val body = JSONObject().apply {
                put("patient_id", patientId)
                put("verified_outcome", outcome)
                put("surgeon_notes", notes)
            }
            val res = ApiClient.request(serverUrl, "/mobile/outcomes", "POST", body, authToken)
            mainHandler.post {
                if (res.ok) {
                    tvOutcomeStatus.text = "Verified outcome ($outcome) saved to database for Continuous Learning."
                    findViewById<EditText>(R.id.et_outcome_notes).setText("")
                } else {
                    tvOutcomeStatus.text = "Failed: ${res.errorMessage}"
                }
            }
        }
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            "flap_alerts_channel",
            "FlapGuard Urgent Surgeon Alerts",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Real-time venous and arterial flap compromise alerts"
            enableVibration(true)
        }
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(channel)
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    1001
                )
            }
        }
    }

    private fun pushSurgeonNotification(id: Int, title: String, message: String) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val notification = NotificationCompat.Builder(this, "flap_alerts_channel")
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle(title)
            .setContentText(message)
            .setStyle(NotificationCompat.BigTextStyle().bigText(message))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(id, notification)
    }
}