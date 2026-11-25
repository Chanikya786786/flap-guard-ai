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
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.util.Base64
import android.view.View
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.floatingactionbutton.ExtendedFloatingActionButton
import com.google.android.material.switchmaterial.SwitchMaterial
import com.google.android.material.textfield.TextInputEditText
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class DashboardActivity : AppCompatActivity() {

    private val executor = Executors.newFixedThreadPool(4)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val alertPollInFlight = AtomicBoolean(false)

    private lateinit var authToken: String
    private var userName: String = "Clinician"
    private var userRole: String = "Surgeon"
    private var userEmail: String = ""

    private val patientsList = mutableListOf<PatientItem>()
    private val notifiedAlertIds = mutableSetOf<Int>()
    private var lastAlertsSignature = ""
    private var pendingCameraUri: Uri? = null

    private var scannedRedness = 0.14
    private var scannedCyanosis = 0.03
    private var scannedPallor = 0.42

    private lateinit var tvToolbarTitle: TextView
    private lateinit var tvToolbarSubtitle: TextView
    private lateinit var progressSync: ProgressBar
    private lateinit var tvLiveBanner: TextView
    private lateinit var bottomNav: BottomNavigationView

    // Tab containers
    private lateinit var sectionWard: FrameLayout
    private lateinit var sectionAssess: ScrollView
    private lateinit var sectionAlerts: LinearLayout
    private lateinit var sectionProfile: ScrollView

    // Ward views
    private lateinit var tvKpiCritical: TextView
    private lateinit var tvKpiHigh: TextView
    private lateinit var tvKpiTotal: TextView
    private lateinit var cardEmptyWard: MaterialCardView
    private lateinit var rvPatients: RecyclerView
    private lateinit var patientAdapter: PatientAdapter

    // Assess views
    private lateinit var spinnerPatient: Spinner
    private lateinit var layoutPreviewFrame: FrameLayout
    private lateinit var ivFlapPreview: ImageView
    private lateinit var tvVisionResult: TextView
    private lateinit var cardPredictionResult: MaterialCardView
    private lateinit var tvPredictionTitle: TextView
    private lateinit var tvPredictionResult: TextView

    // Alerts views
    private lateinit var tvAlertsSummary: TextView
    private lateinit var cardEmptyAlerts: MaterialCardView
    private lateinit var rvAlerts: RecyclerView
    private lateinit var alertAdapter: AlertAdapter

    // Profile views
    private lateinit var tvProfileInitials: TextView
    private lateinit var tvProfileName: TextView
    private lateinit var tvProfileEmail: TextView
    private lateinit var tvProfileRole: TextView
    private lateinit var tvProfileSystemStats: TextView
    private lateinit var switchUsbMode: SwitchMaterial
    private lateinit var tvActiveServerLabel: TextView

    private val pollRunnable = object : Runnable {
        override fun run() {
            fetchAlerts(silent = true)
            mainHandler.postDelayed(this, 15_000L)
        }
    }

    private val takeFullPhotoLauncher = registerForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { success: Boolean ->
        val uri = pendingCameraUri
        if (success && uri != null) {
            decodeAndAnalyzeUri(uri)
        }
    }

    private val galleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri != null) {
            decodeAndAnalyzeUri(uri)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_dashboard)

        val prefs = getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
        authToken = prefs.getString("auth_token", "").orEmpty()
        userName = prefs.getString("user_name", "Clinician") ?: "Clinician"
        userRole = prefs.getString("user_role", "Surgeon") ?: "Surgeon"
        userEmail = prefs.getString("user_email", "") ?: ""

        if (authToken.isBlank()) {
            logoutToLogin()
            return
        }

        createNotificationChannel()
        requestNotificationPermissionIfNeeded()
        bindViews()
        setupAdapters()
        setupNavigation()
        setupActions()
        populateStaticProfile()

        syncAll()
    }

    override fun onResume() {
        super.onResume()
        mainHandler.postDelayed(pollRunnable, 15_000L)
    }

    override fun onPause() {
        super.onPause()
        mainHandler.removeCallbacks(pollRunnable)
    }

    override fun onDestroy() {
        super.onDestroy()
        mainHandler.removeCallbacks(pollRunnable)
        executor.shutdownNow()
    }

    private fun serverUrl(): String = ApiClient.getServerUrl(this)

    private fun bindViews() {
        tvToolbarTitle = findViewById(R.id.tv_toolbar_title)
        tvToolbarSubtitle = findViewById(R.id.tv_toolbar_subtitle)
        progressSync = findViewById(R.id.progress_sync)
        tvLiveBanner = findViewById(R.id.tv_live_alert_banner)
        bottomNav = findViewById(R.id.bottom_nav)

        sectionWard = findViewById(R.id.section_ward)
        sectionAssess = findViewById(R.id.section_assess)
        sectionAlerts = findViewById(R.id.section_alerts)
        sectionProfile = findViewById(R.id.section_profile)

        tvKpiCritical = findViewById(R.id.tv_kpi_critical)
        tvKpiHigh = findViewById(R.id.tv_kpi_high)
        tvKpiTotal = findViewById(R.id.tv_kpi_total)
        cardEmptyWard = findViewById(R.id.card_empty_ward)
        rvPatients = findViewById(R.id.rv_patients)

        spinnerPatient = findViewById(R.id.spinner_patient)
        layoutPreviewFrame = findViewById(R.id.layout_preview_frame)
        ivFlapPreview = findViewById(R.id.iv_flap_preview)
        tvVisionResult = findViewById(R.id.tv_vision_result)
        cardPredictionResult = findViewById(R.id.card_prediction_result)
        tvPredictionTitle = findViewById(R.id.tv_prediction_title)
        tvPredictionResult = findViewById(R.id.tv_prediction_result)

        tvAlertsSummary = findViewById(R.id.tv_alerts_summary)
        cardEmptyAlerts = findViewById(R.id.card_empty_alerts)
        rvAlerts = findViewById(R.id.rv_alerts)

        tvProfileInitials = findViewById(R.id.tv_profile_initials)
        tvProfileName = findViewById(R.id.tv_profile_name)
        tvProfileEmail = findViewById(R.id.tv_profile_email)
        tvProfileRole = findViewById(R.id.tv_profile_role)
        tvProfileSystemStats = findViewById(R.id.tv_profile_system_stats)
        switchUsbMode = findViewById(R.id.switch_usb_mode)
        tvActiveServerLabel = findViewById(R.id.tv_active_server_label)
    }

    private fun setupAdapters() {
        patientAdapter = PatientAdapter(
            onCardClick = { patient -> showPatientDetailDialog(patient) },
            onAssessClick = { patient -> openAssessForPatient(patient) },
            onDeleteClick = { patient -> confirmDeletePatient(patient) }
        )
        rvPatients.layoutManager = LinearLayoutManager(this)
        rvPatients.adapter = patientAdapter

        alertAdapter = AlertAdapter(
            onAcknowledge = { alert -> acknowledgeAlert(alert.id) }
        )
        rvAlerts.layoutManager = LinearLayoutManager(this)
        rvAlerts.adapter = alertAdapter
    }

    private fun setupNavigation() {
        bottomNav.setOnItemSelectedListener { item ->
            sectionWard.visibility = if (item.itemId == R.id.nav_ward) View.VISIBLE else View.GONE
            sectionAssess.visibility = if (item.itemId == R.id.nav_assess) View.VISIBLE else View.GONE
            sectionAlerts.visibility = if (item.itemId == R.id.nav_alerts) View.VISIBLE else View.GONE
            sectionProfile.visibility = if (item.itemId == R.id.nav_profile) View.VISIBLE else View.GONE

            when (item.itemId) {
                R.id.nav_ward -> {
                    tvToolbarTitle.text = "FlapGuard Ward"
                    tvToolbarSubtitle.text = "Post-Op Free-Flap Risk Stratification"
                    fetchPatients()
                }
                R.id.nav_assess -> {
                    tvToolbarTitle.text = "Flap Assessment"
                    tvToolbarSubtitle.text = "CIE-LAB Colorimetry + Multisensor Fusion"
                }
                R.id.nav_alerts -> {
                    tvToolbarTitle.text = "Surgeon Alerts"
                    tvToolbarSubtitle.text = "Real-Time Compromise Escalations"
                    fetchAlerts(silent = false)
                }
                R.id.nav_profile -> {
                    tvToolbarTitle.text = "Clinician Profile"
                    tvToolbarSubtitle.text = "$userName • $userRole"
                    fetchProfileStats()
                }
            }
            true
        }
    }

    private fun setupActions() {
        findViewById<ImageButton>(R.id.btn_refresh_all).setOnClickListener { syncAll() }

        tvLiveBanner.setOnClickListener {
            bottomNav.selectedItemId = R.id.nav_alerts
        }

        findViewById<ExtendedFloatingActionButton>(R.id.fab_add_patient).setOnClickListener {
            showAddPatientDialog()
        }

        findViewById<MaterialButton>(R.id.btn_open_camera).setOnClickListener { launchFullResCamera() }
        findViewById<MaterialButton>(R.id.btn_pick_gallery).setOnClickListener { galleryLauncher.launch("image/*") }
        findViewById<MaterialButton>(R.id.btn_submit_reading).setOnClickListener { submitSensorReading() }

        findViewById<MaterialButton>(R.id.btn_sim_venous).setOnClickListener {
            triggerSimulation("venous_deteriorating", 7)
        }
        findViewById<MaterialButton>(R.id.btn_sim_arterial).setOnClickListener {
            triggerSimulation("arterial_deteriorating", 7)
        }

        findViewById<MaterialButton>(R.id.btn_log_outcome_dialog).setOnClickListener {
            showLogOutcomeDialog()
        }

        findViewById<MaterialButton>(R.id.btn_load_samples).setOnClickListener {
            loadSampleCases()
        }

        findViewById<MaterialButton>(R.id.btn_clear_ward).setOnClickListener {
            confirmClearWard()
        }

        switchUsbMode.isChecked = ApiClient.isLocalUsbMode(this)
        updateServerModeLabel()
        switchUsbMode.setOnCheckedChangeListener { _, isChecked ->
            ApiClient.setLocalUsbMode(this, isChecked)
            updateServerModeLabel()
            toast(if (isChecked) "Switched to Offline USB Mode" else "Switched to Live Cloud")
            syncAll()
        }

        findViewById<MaterialButton>(R.id.btn_change_password).setOnClickListener {
            showChangePasswordDialog()
        }

        findViewById<MaterialButton>(R.id.btn_logout).setOnClickListener {
            logoutToLogin()
        }
    }

    private fun populateStaticProfile() {
        tvProfileName.text = userName
        tvProfileRole.text = userRole
        tvProfileEmail.text = userEmail.ifBlank { "Signed in clinician" }
        val parts = userName.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
        val initials = when {
            parts.size >= 2 -> "${parts[0].first()}${parts[1].first()}"
            parts.size == 1 -> parts[0].take(2)
            else -> "FG"
        }.uppercase(Locale.US)
        tvProfileInitials.text = initials
    }

    private fun updateServerModeLabel() {
        tvActiveServerLabel.text = if (ApiClient.isLocalUsbMode(this)) {
            "Active: Offline USB (http://127.0.0.1:5000)"
        } else {
            "Active: Live Cloud (flap-guard-ai.onrender.com)"
        }
    }

    private fun syncAll() {
        fetchPatients()
        fetchAlerts(silent = false)
        fetchProfileStats()
    }

    private fun handleUnauthorized(statusCode: Int): Boolean {
        if (statusCode == 401) {
            toast("Session expired. Please sign in again.")
            logoutToLogin()
            return true
        }
        return false
    }

    private fun logoutToLogin() {
        getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE).edit()
            .remove("auth_token")
            .apply()
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    private fun fetchPatients() {
        progressSync.visibility = View.VISIBLE
        executor.execute {
            val res = ApiClient.request(serverUrl(), "/mobile/patients", "GET", null, authToken)
            mainHandler.post {
                progressSync.visibility = View.GONE
                if (!res.ok) {
                    if (!handleUnauthorized(res.statusCode)) {
                        toast("Ward sync: ${res.errorMessage}")
                    }
                    return@post
                }
                val arr = res.json.optJSONArray("patients") ?: JSONArray()
                parseAndRenderPatients(arr)
            }
        }
    }

    private fun parseAndRenderPatients(arr: JSONArray) {
        patientsList.clear()
        var critical = 0
        var highMod = 0

        for (i in 0 until arr.length()) {
            val p = arr.getJSONObject(i)
            val latest = p.optJSONObject("latest_reading")
            val tier = latest?.optString("risk_tier") ?: "Unassessed"
            if (tier == "Critical") critical++
            if (tier == "High" || tier == "Moderate") highMod++

            patientsList.add(
                PatientItem(
                    id = p.getInt("id"),
                    mrn = p.optString("mrn"),
                    fullName = p.optString("full_name"),
                    age = p.optInt("age", 0),
                    flapType = p.optString("flap_type"),
                    surgerySite = p.optString("surgery_site"),
                    hoursPostOp = p.optDouble("hours_post_op", 0.0),
                    riskTier = tier,
                    predictedClass = latest?.optString("predicted_class") ?: "Unassessed",
                    riskScorePct = latest?.optDouble("risk_score_pct", 0.0) ?: 0.0,
                    tempDeltaC = if (latest != null) latest.optDouble("temp_delta_c") else null,
                    spo2Pct = if (latest != null) latest.optDouble("spo2_pct") else null,
                    dopplerCmS = if (latest != null) latest.optDouble("doppler_flow_cm_s") else null,
                    visualScore = if (latest != null) latest.optDouble("visual_perfusion_score") else null,
                    alertMessage = latest?.optString("alert_message").orEmpty()
                )
            )
        }

        tvKpiCritical.text = critical.toString()
        tvKpiHigh.text = highMod.toString()
        tvKpiTotal.text = patientsList.size.toString()

        cardEmptyWard.visibility = if (patientsList.isEmpty()) View.VISIBLE else View.GONE
        rvPatients.visibility = if (patientsList.isEmpty()) View.GONE else View.VISIBLE
        patientAdapter.submitList(patientsList)

        val labels = if (patientsList.isEmpty()) {
            listOf("No patients in ward yet")
        } else {
            patientsList.map { "${it.fullName} (${it.mrn})" }
        }
        val prevIdx = spinnerPatient.selectedItemPosition
        spinnerPatient.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
        if (prevIdx in patientsList.indices) {
            spinnerPatient.setSelection(prevIdx)
        }
    }

    private fun openAssessForPatient(patient: PatientItem) {
        val idx = patientsList.indexOfFirst { it.id == patient.id }
        if (idx >= 0) {
            spinnerPatient.setSelection(idx)
        }
        bottomNav.selectedItemId = R.id.nav_assess
    }

    private fun showPatientDetailDialog(patient: PatientItem) {
        val vitalsText = if (patient.tempDeltaC != null) {
            String.format(
                Locale.US,
                "AI Prediction: %s (%.1f%% risk)\nRisk Tier: %s\n\nIR ΔT: %.2f °C\nTissue SpO₂: %.1f%%\nDoppler Flow: %.1f cm/s\nVisual Perfusion: %.1f / 100\n\n%s",
                patient.predictedClass,
                patient.riskScorePct,
                patient.riskTier,
                patient.tempDeltaC,
                patient.spo2Pct ?: 0.0,
                patient.dopplerCmS ?: 0.0,
                patient.visualScore ?: 0.0,
                patient.alertMessage
            )
        } else {
            "No sensor assessments recorded yet."
        }

        MaterialAlertDialogBuilder(this)
            .setTitle("${patient.fullName} (${patient.mrn})")
            .setMessage(
                "Age: ${patient.age}y  •  Post-Op: ${patient.hoursPostOp}h\n" +
                    "Flap: ${patient.flapType}\nSite: ${patient.surgerySite}\n\n$vitalsText"
            )
            .setPositiveButton("Assess Flap") { _, _ -> openAssessForPatient(patient) }
            .setNeutralButton("Close", null)
            .setNegativeButton("Remove") { _, _ -> confirmDeletePatient(patient) }
            .show()
    }

    private fun showAddPatientDialog() {
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(52, 24, 52, 8)
        }

        fun makeField(hint: String, inputType: Int = InputType.TYPE_CLASS_TEXT, defaultVal: String = ""): EditText {
            return EditText(this).apply {
                this.hint = hint
                this.inputType = inputType
                setText(defaultVal)
            }
        }

        val etMrn = makeField("MRN (e.g., MRN-2026-201)")
        val etName = makeField("Patient Full Name")
        val etAge = makeField("Age (years)", InputType.TYPE_CLASS_NUMBER, "48")
        val flapTypes = listOf(
            "Radial Forearm Free Flap",
            "Fibula Osteocutaneous Flap",
            "Anterolateral Thigh (ALT) Flap",
            "Deep Circumflex Iliac Artery (DCIA) Flap",
            "Pectoralis Major Flap"
        )
        val spFlap = Spinner(this).apply {
            adapter = ArrayAdapter(this@DashboardActivity, android.R.layout.simple_spinner_dropdown_item, flapTypes)
        }
        val etSite = makeField("Surgery Site (e.g., Right Mandible)")
        val etHours = makeField("Hours Post-Op", InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL, "4.0")

        listOf(etMrn, etName, etAge, spFlap, etSite, etHours).forEach { container.addView(it) }

        MaterialAlertDialogBuilder(this)
            .setTitle("Admit Post-Op Flap Patient")
            .setView(container)
            .setPositiveButton("Add Patient") { _, _ ->
                val payload = JSONObject().apply {
                    put("mrn", etMrn.text.toString().trim().uppercase(Locale.US))
                    put("full_name", etName.text.toString().trim())
                    put("age", etAge.text.toString().toIntOrNull() ?: 0)
                    put("flap_type", spFlap.selectedItem.toString())
                    put("surgery_site", etSite.text.toString().trim())
                    put("hours_post_op", etHours.text.toString().toDoubleOrNull() ?: 1.0)
                }
                progressSync.visibility = View.VISIBLE
                executor.execute {
                    val res = ApiClient.request(serverUrl(), "/mobile/patients", "POST", payload, authToken)
                    mainHandler.post {
                        progressSync.visibility = View.GONE
                        if (res.ok) {
                            toast("Patient added to ward.")
                            fetchPatients()
                        } else {
                            toast("Could not add patient: ${res.errorMessage}")
                        }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun confirmDeletePatient(patient: PatientItem) {
        MaterialAlertDialogBuilder(this)
            .setTitle("Remove Patient?")
            .setMessage("Remove ${patient.fullName} (${patient.mrn}) and all associated readings from ward monitoring?")
            .setPositiveButton("Remove") { _, _ ->
                progressSync.visibility = View.VISIBLE
                executor.execute {
                    val res = ApiClient.request(
                        serverUrl(),
                        "/mobile/patients/${patient.id}/delete",
                        "POST",
                        JSONObject(),
                        authToken
                    )
                    mainHandler.post {
                        progressSync.visibility = View.GONE
                        if (res.ok) {
                            toast("Removed ${patient.fullName}.")
                            syncAll()
                        } else {
                            toast("Delete failed: ${res.errorMessage}")
                        }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun launchFullResCamera() {
        try {
            val dir = File(cacheDir, "camera").apply { mkdirs() }
            val file = File(dir, "flap_${System.currentTimeMillis()}.jpg")
            val uri = FileProvider.getUriForFile(this, "${packageName}.fileprovider", file)
            pendingCameraUri = uri
            takeFullPhotoLauncher.launch(uri)
        } catch (e: Exception) {
            toast("Camera unavailable: ${e.message}")
        }
    }

    private fun decodeAndAnalyzeUri(uri: Uri) {
        tvVisionResult.text = "Analyzing centered 50% flap ROI via CIE-LAB OpenCV..."
        executor.execute {
            try {
                val rawBitmap = contentResolver.openInputStream(uri)?.use { stream ->
                    BitmapFactory.decodeStream(stream)
                }
                if (rawBitmap == null) {
                    mainHandler.post { tvVisionResult.text = "Could not decode selected image." }
                    return@execute
                }

                // Preserve aspect ratio, cap max dimension at 640px for fast upload + crisp blur metrics
                val maxDim = 640
                val ratio = minOf(maxDim.toFloat() / rawBitmap.width, maxDim.toFloat() / rawBitmap.height, 1.0f)
                val targetW = (rawBitmap.width * ratio).toInt().coerceAtLeast(64)
                val targetH = (rawBitmap.height * ratio).toInt().coerceAtLeast(64)
                val scaled = Bitmap.createScaledBitmap(rawBitmap, targetW, targetH, true)

                val baos = ByteArrayOutputStream()
                scaled.compress(Bitmap.CompressFormat.JPEG, 88, baos)
                val encoded = Base64.encodeToString(baos.toByteArray(), Base64.NO_WRAP)

                mainHandler.post {
                    ivFlapPreview.setImageBitmap(scaled)
                    layoutPreviewFrame.visibility = View.VISIBLE
                }

                val body = JSONObject().put("image_base64", encoded)
                val res = ApiClient.request(serverUrl(), "/mobile/analyze-image", "POST", body, authToken)

                mainHandler.post {
                    if (res.ok) {
                        scannedRedness = res.json.optDouble("visual_redness_index", 0.14)
                        scannedCyanosis = res.json.optDouble("visual_cyanosis_index", 0.03)
                        scannedPallor = res.json.optDouble("visual_pallor_index", 0.42)
                        val score = res.json.optDouble("visual_perfusion_score", 0.0)
                        val triage = res.json.optString("visual_triage_label", "Unknown")
                        val qStatus = res.json.optString("quality_status", "Good")
                        val warnings = res.json.optJSONArray("quality_warnings")
                        val warnText = if (warnings != null && warnings.length() > 0) {
                            "\nQuality Note: ${warnings.optString(0)}"
                        } else {
                            ""
                        }

                        tvVisionResult.text = String.format(
                            Locale.US,
                            "Triage: %s\nVisual Perfusion: %.1f / 100  •  Capture Quality: %s\nRedness %.3f  •  Cyanosis %.3f  •  Pallor %.3f%s",
                            triage, score, qStatus, scannedRedness, scannedCyanosis, scannedPallor, warnText
                        )
                    } else {
                        tvVisionResult.text = "Image analysis failed: ${res.errorMessage}"
                    }
                }
            } catch (e: Exception) {
                mainHandler.post { tvVisionResult.text = "Image error: ${e.message}" }
            }
        }
    }

    private fun selectedPatient(): PatientItem? {
        val idx = spinnerPatient.selectedItemPosition
        return if (idx in patientsList.indices) patientsList[idx] else null
    }

    private fun submitSensorReading() {
        val patient = selectedPatient()
        if (patient == null) {
            toast("Add a patient in the Ward tab first.")
            return
        }

        val irTemp = findViewById<TextInputEditText>(R.id.et_ir_temp).text?.toString()?.toDoubleOrNull()
        val ctrlTemp = findViewById<TextInputEditText>(R.id.et_ctrl_temp).text?.toString()?.toDoubleOrNull()
        val spo2 = findViewById<TextInputEditText>(R.id.et_spo2).text?.toString()?.toDoubleOrNull()
        val doppler = findViewById<TextInputEditText>(R.id.et_doppler).text?.toString()?.toDoubleOrNull()
        val hours = findViewById<TextInputEditText>(R.id.et_hours).text?.toString()?.toDoubleOrNull() ?: 12.0

        if (irTemp == null || ctrlTemp == null || spo2 == null || doppler == null) {
            toast("Enter valid numeric sensor values.")
            return
        }

        progressSync.visibility = View.VISIBLE
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
                serverUrl(),
                "/mobile/patients/${patient.id}/reading",
                "POST",
                payload,
                authToken
            )
            mainHandler.post {
                progressSync.visibility = View.GONE
                if (res.ok) {
                    showPredictionCard(res.json.getJSONObject("reading"))
                    fetchPatients()
                    fetchAlerts(silent = false)
                } else {
                    toast("Assessment failed: ${res.errorMessage}")
                }
            }
        }
    }

    private fun triggerSimulation(scenario: String, step: Int) {
        val patient = selectedPatient()
        if (patient == null) {
            toast("Add a patient in the Ward tab first.")
            return
        }
        progressSync.visibility = View.VISIBLE
        executor.execute {
            val payload = JSONObject().put("scenario", scenario).put("step", step)
            val res = ApiClient.request(
                serverUrl(),
                "/mobile/patients/${patient.id}/simulate",
                "POST",
                payload,
                authToken
            )
            mainHandler.post {
                progressSync.visibility = View.GONE
                if (res.ok) {
                    showPredictionCard(res.json.getJSONObject("reading"))
                    fetchPatients()
                    fetchAlerts(silent = false)
                } else {
                    toast("Simulation failed: ${res.errorMessage}")
                }
            }
        }
    }

    private fun showPredictionCard(r: JSONObject) {
        val predClass = r.optString("predicted_class")
        val tier = r.optString("risk_tier")
        val riskPct = r.optDouble("risk_score_pct", 0.0)
        val msg = r.optString("alert_message")

        cardPredictionResult.visibility = View.VISIBLE
        val (bg, stroke, titleColor) = when (tier) {
            "Critical" -> Triple("#FEF2F2", "#FCA5A5", "#B91C1C")
            "High" -> Triple("#FFFBEB", "#FCD34D", "#B45309")
            "Moderate" -> Triple("#EFF6FF", "#93C5FD", "#1D4ED8")
            else -> Triple("#F0FDF4", "#86EFAC", "#15803D")
        }
        cardPredictionResult.setCardBackgroundColor(Color.parseColor(bg))
        cardPredictionResult.strokeColor = Color.parseColor(stroke)
        tvPredictionTitle.setTextColor(Color.parseColor(titleColor))
        tvPredictionTitle.text = "$predClass  •  $tier Risk (${String.format(Locale.US, "%.0f%%", riskPct)})"
        tvPredictionResult.text = msg
    }

    private fun fetchAlerts(silent: Boolean) {
        if (!alertPollInFlight.compareAndSet(false, true)) return
        executor.execute {
            val res = ApiClient.request(serverUrl(), "/mobile/alerts", "GET", null, authToken)
            alertPollInFlight.set(false)
            mainHandler.post {
                if (!res.ok) {
                    if (!silent && !handleUnauthorized(res.statusCode)) {
                        toast("Alert sync: ${res.errorMessage}")
                    }
                    return@post
                }
                val unack = res.json.optInt("unacknowledged_count", 0)
                val alertsArr = res.json.optJSONArray("alerts") ?: JSONArray()

                if (unack > 0) {
                    tvLiveBanner.visibility = View.VISIBLE
                    tvLiveBanner.text = "URGENT: $unack Unacknowledged Flap Alert(s) — Tap to Inspect"
                } else {
                    tvLiveBanner.visibility = View.GONE
                }

                val sig = "$unack:${alertsArr.toString().hashCode()}"
                if (sig == lastAlertsSignature && silent) return@post
                lastAlertsSignature = sig

                val list = mutableListOf<AlertItem>()
                for (i in 0 until alertsArr.length()) {
                    val a = alertsArr.getJSONObject(i)
                    val id = a.getInt("id")
                    val pName = a.optString("patient_name")
                    val tier = a.optString("risk_tier")
                    val msg = a.optString("alert_message")
                    val acked = a.optBoolean("alert_acknowledged", false)
                    val ackBy = a.optString("acknowledged_by", "Surgeon")

                    if (!acked && notifiedAlertIds.add(id)) {
                        pushSurgeonNotification(id, "$tier Flap Alert: $pName", msg)
                    }
                    list.add(AlertItem(id, pName, tier, msg, acked, ackBy))
                }

                tvAlertsSummary.text = "Surgeon Escalations  •  $unack Active"
                cardEmptyAlerts.visibility = if (list.isEmpty()) View.VISIBLE else View.GONE
                rvAlerts.visibility = if (list.isEmpty()) View.GONE else View.VISIBLE
                alertAdapter.submitList(list)
            }
        }
    }

    private fun acknowledgeAlert(readingId: Int) {
        progressSync.visibility = View.VISIBLE
        executor.execute {
            val res = ApiClient.request(
                serverUrl(),
                "/mobile/alerts/$readingId/acknowledge",
                "POST",
                JSONObject(),
                authToken
            )
            mainHandler.post {
                progressSync.visibility = View.GONE
                if (res.ok) {
                    toast("Alert #$readingId acknowledged.")
                    lastAlertsSignature = ""
                    fetchAlerts(silent = false)
                } else {
                    toast("Failed: ${res.errorMessage}")
                }
            }
        }
    }

    private fun fetchProfileStats() {
        executor.execute {
            val res = ApiClient.request(serverUrl(), "/mobile/profile", "GET", null, authToken)
            mainHandler.post {
                if (!res.ok) return@post
                val userObj = res.json.optJSONObject("user")
                if (userObj != null) {
                    userName = userObj.optString("full_name", userName)
                    userRole = userObj.optString("role", userRole)
                    userEmail = userObj.optString("email", userEmail)
                    populateStaticProfile()
                }
                val ver = res.json.optString("model_version", "v1.0")
                val acc = res.json.optDouble("model_accuracy", 0.0) * 100.0
                val pCount = res.json.optInt("patients_count", 0)
                val aCount = res.json.optInt("unacknowledged_alerts", 0)
                tvProfileSystemStats.text = String.format(
                    Locale.US,
                    "AI Model: %s (%.1f%% validation accuracy)\nMonitored Ward Patients: %d\nUnacknowledged Alerts: %d",
                    ver, acc, pCount, aCount
                )
            }
        }
    }

    private fun showLogOutcomeDialog() {
        if (patientsList.isEmpty()) {
            toast("Add a patient in the Ward tab first.")
            return
        }
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(52, 24, 52, 8)
        }
        val spPatient = Spinner(this).apply {
            adapter = ArrayAdapter(
                this@DashboardActivity,
                android.R.layout.simple_spinner_dropdown_item,
                patientsList.map { "${it.fullName} (${it.mrn})" }
            )
        }
        val outcomes = listOf("Normal", "Venous Compromise", "Arterial Compromise")
        val spOutcome = Spinner(this).apply {
            adapter = ArrayAdapter(this@DashboardActivity, android.R.layout.simple_spinner_dropdown_item, outcomes)
        }
        val etNotes = EditText(this).apply { hint = "Surgeon intraoperative / clinical notes" }

        container.addView(spPatient)
        container.addView(spOutcome)
        container.addView(etNotes)

        MaterialAlertDialogBuilder(this)
            .setTitle("Log Verified Post-Op Outcome")
            .setView(container)
            .setPositiveButton("Save Outcome") { _, _ ->
                val patient = patientsList[spPatient.selectedItemPosition]
                val body = JSONObject().apply {
                    put("patient_id", patient.id)
                    put("verified_outcome", spOutcome.selectedItem.toString())
                    put("surgeon_notes", etNotes.text.toString().trim())
                }
                progressSync.visibility = View.VISIBLE
                executor.execute {
                    val res = ApiClient.request(serverUrl(), "/mobile/outcomes", "POST", body, authToken)
                    mainHandler.post {
                        progressSync.visibility = View.GONE
                        if (res.ok) {
                            toast("Verified outcome saved for retraining.")
                        } else {
                            toast("Failed: ${res.errorMessage}")
                        }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun loadSampleCases() {
        progressSync.visibility = View.VISIBLE
        executor.execute {
            val res = ApiClient.request(serverUrl(), "/mobile/ward/load-samples", "POST", JSONObject(), authToken)
            mainHandler.post {
                progressSync.visibility = View.GONE
                if (res.ok) {
                    val created = res.json.optInt("created_count", 0)
                    toast(if (created > 0) "Loaded $created sample OMFS cases." else "Sample cases already in ward.")
                    syncAll()
                    bottomNav.selectedItemId = R.id.nav_ward
                } else {
                    toast("Failed: ${res.errorMessage}")
                }
            }
        }
    }

    private fun confirmClearWard() {
        MaterialAlertDialogBuilder(this)
            .setTitle("Clear Entire Ward?")
            .setMessage("This will permanently remove all patient records, readings, and alerts from the active database.")
            .setPositiveButton("Clear Ward") { _, _ ->
                progressSync.visibility = View.VISIBLE
                executor.execute {
                    val res = ApiClient.request(serverUrl(), "/mobile/ward/clear", "POST", JSONObject(), authToken)
                    mainHandler.post {
                        progressSync.visibility = View.GONE
                        if (res.ok) {
                            toast("Ward cleared.")
                            lastAlertsSignature = ""
                            syncAll()
                            bottomNav.selectedItemId = R.id.nav_ward
                        } else {
                            toast("Failed: ${res.errorMessage}")
                        }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showChangePasswordDialog() {
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(52, 24, 52, 8)
        }
        val etCurr = EditText(this).apply {
            hint = "Current Password"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val etNew = EditText(this).apply {
            hint = "New Password (8+ chars)"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        container.addView(etCurr)
        container.addView(etNew)

        MaterialAlertDialogBuilder(this)
            .setTitle("Change Password")
            .setView(container)
            .setPositiveButton("Update") { _, _ ->
                val body = JSONObject()
                    .put("current_password", etCurr.text.toString())
                    .put("new_password", etNew.text.toString())
                executor.execute {
                    val res = ApiClient.request(serverUrl(), "/mobile/profile/password", "POST", body, authToken)
                    mainHandler.post {
                        toast(if (res.ok) "Password updated." else res.errorMessage)
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
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
