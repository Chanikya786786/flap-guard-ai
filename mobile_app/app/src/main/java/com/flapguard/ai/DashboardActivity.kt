package com.flapguard.ai

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.ArrayAdapter
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.floatingactionbutton.ExtendedFloatingActionButton
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

class DashboardActivity : AppCompatActivity() {

    private val executor = Executors.newFixedThreadPool(4)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val alertPollInFlight = AtomicBoolean(false)
    private val syncCounter = AtomicInteger(0)

    private lateinit var authToken: String
    private var userName: String = "Clinician"
    private var userRole: String = "Surgeon"
    private var userEmail: String = ""
    private var userPhotoDataUri: String = ""

    private val patientsList = mutableListOf<PatientItem>()
    private var selectedPatientIndex: Int = 0
    private val notifiedAlertIds = mutableSetOf<Int>()
    private var lastAlertsSignature = ""
    private var pendingFlapCameraUri: Uri? = null
    private var pendingProfileCameraUri: Uri? = null

    private var scannedRedness = 0.14
    private var scannedCyanosis = 0.03
    private var scannedPallor = 0.42

    private lateinit var rootView: View
    private lateinit var tvToolbarTitle: TextView
    private lateinit var tvToolbarSubtitle: TextView
    private lateinit var btnRefreshAll: ImageButton
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
    private lateinit var cardSelectPatient: MaterialCardView
    private lateinit var tvSelectedPatient: TextView
    private lateinit var layoutPreviewFrame: FrameLayout
    private lateinit var ivFlapPreview: ImageView
    private lateinit var tvVisionResult: TextView
    private lateinit var tilIrTemp: TextInputLayout
    private lateinit var tilCtrlTemp: TextInputLayout
    private lateinit var tilSpo2: TextInputLayout
    private lateinit var tilDoppler: TextInputLayout
    private lateinit var etIrTemp: TextInputEditText
    private lateinit var etCtrlTemp: TextInputEditText
    private lateinit var etSpo2: TextInputEditText
    private lateinit var etDoppler: TextInputEditText
    private lateinit var etHours: TextInputEditText
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
    private lateinit var ivProfilePhoto: ImageView
    private lateinit var tvProfileName: TextView
    private lateinit var tvProfileEmail: TextView
    private lateinit var tvProfileRole: TextView
    private lateinit var tvStatModel: TextView
    private lateinit var tvStatPatients: TextView
    private lateinit var tvStatAlerts: TextView
    private lateinit var tvActiveServerLabel: TextView

    private val pollRunnable = object : Runnable {
        override fun run() {
            fetchAlerts(silent = true)
            mainHandler.postDelayed(this, 15_000L)
        }
    }

    private val flapCameraLauncher = registerForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { success: Boolean ->
        val uri = pendingFlapCameraUri
        if (success && uri != null) {
            analyzeFlapImageUri(uri)
        }
    }

    private val flapGalleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri != null) {
            analyzeFlapImageUri(uri)
        }
    }

    private val profileCameraLauncher = registerForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { success: Boolean ->
        val uri = pendingProfileCameraUri
        if (success && uri != null) {
            uploadProfilePhotoUri(uri)
        }
    }

    private val profileGalleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri != null) {
            uploadProfilePhotoUri(uri)
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
        userPhotoDataUri = prefs.getString("user_photo", "") ?: ""

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
        populateProfileHeader()
        updateServerModeLabel()

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

    private fun beginSync() {
        if (syncCounter.incrementAndGet() > 0) {
            mainHandler.post {
                btnRefreshAll.visibility = View.GONE
                progressSync.visibility = View.VISIBLE
            }
        }
    }

    private fun endSync() {
        if (syncCounter.decrementAndGet() <= 0) {
            syncCounter.set(0)
            mainHandler.post {
                progressSync.visibility = View.GONE
                btnRefreshAll.visibility = View.VISIBLE
            }
        }
    }

    private fun bindViews() {
        rootView = findViewById(R.id.dashboard_root)
        tvToolbarTitle = findViewById(R.id.tv_toolbar_title)
        tvToolbarSubtitle = findViewById(R.id.tv_toolbar_subtitle)
        btnRefreshAll = findViewById(R.id.btn_refresh_all)
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

        cardSelectPatient = findViewById(R.id.card_select_patient)
        tvSelectedPatient = findViewById(R.id.tv_selected_patient)
        layoutPreviewFrame = findViewById(R.id.layout_preview_frame)
        ivFlapPreview = findViewById(R.id.iv_flap_preview)
        tvVisionResult = findViewById(R.id.tv_vision_result)
        tilIrTemp = findViewById(R.id.til_ir_temp)
        tilCtrlTemp = findViewById(R.id.til_ctrl_temp)
        tilSpo2 = findViewById(R.id.til_spo2)
        tilDoppler = findViewById(R.id.til_doppler)
        etIrTemp = findViewById(R.id.et_ir_temp)
        etCtrlTemp = findViewById(R.id.et_ctrl_temp)
        etSpo2 = findViewById(R.id.et_spo2)
        etDoppler = findViewById(R.id.et_doppler)
        etHours = findViewById(R.id.et_hours)
        cardPredictionResult = findViewById(R.id.card_prediction_result)
        tvPredictionTitle = findViewById(R.id.tv_prediction_title)
        tvPredictionResult = findViewById(R.id.tv_prediction_result)

        tvAlertsSummary = findViewById(R.id.tv_alerts_summary)
        cardEmptyAlerts = findViewById(R.id.card_empty_alerts)
        rvAlerts = findViewById(R.id.rv_alerts)

        tvProfileInitials = findViewById(R.id.tv_profile_initials)
        ivProfilePhoto = findViewById(R.id.iv_profile_photo)
        tvProfileName = findViewById(R.id.tv_profile_name)
        tvProfileEmail = findViewById(R.id.tv_profile_email)
        tvProfileRole = findViewById(R.id.tv_profile_role)
        tvStatModel = findViewById(R.id.tv_stat_model)
        tvStatPatients = findViewById(R.id.tv_stat_patients)
        tvStatAlerts = findViewById(R.id.tv_stat_alerts)
        tvActiveServerLabel = findViewById(R.id.tv_active_server_label)
    }

    private fun setupAdapters() {
        patientAdapter = PatientAdapter(
            onCardClick = { patient -> showPatientDetailSheet(patient) },
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
                    tvToolbarSubtitle.text = "Post-Op Free-Flap Monitoring"
                    fetchPatients()
                }
                R.id.nav_assess -> {
                    tvToolbarTitle.text = "Flap Assessment"
                    tvToolbarSubtitle.text = "CIE-LAB Colorimetry + Sensor Fusion"
                    sectionAssess.post { sectionAssess.scrollTo(0, 0) }
                }
                R.id.nav_alerts -> {
                    tvToolbarTitle.text = "Surgeon Alerts"
                    tvToolbarSubtitle.text = "Real-Time Compromise Escalations"
                    fetchAlerts(silent = false)
                }
                R.id.nav_profile -> {
                    tvToolbarTitle.text = "Clinician Profile"
                    tvToolbarSubtitle.text = "$userName • $userRole"
                    sectionProfile.post { sectionProfile.scrollTo(0, 0) }
                    fetchProfileStats()
                }
            }
            true
        }
    }

    private fun setupActions() {
        btnRefreshAll.setOnClickListener { syncAll() }

        tvLiveBanner.setOnClickListener {
            bottomNav.selectedItemId = R.id.nav_alerts
        }

        findViewById<ExtendedFloatingActionButton>(R.id.fab_add_patient).setOnClickListener {
            showAddPatientSheet()
        }

        cardSelectPatient.setOnClickListener { showPatientPickerDialog() }

        findViewById<MaterialButton>(R.id.btn_open_camera).setOnClickListener { launchFlapCamera() }
        findViewById<MaterialButton>(R.id.btn_pick_gallery).setOnClickListener { flapGalleryLauncher.launch("image/*") }
        findViewById<MaterialButton>(R.id.btn_submit_reading).setOnClickListener { submitSensorReading() }

        findViewById<MaterialButton>(R.id.btn_sim_venous).setOnClickListener {
            triggerSimulation("venous_deteriorating", 7)
        }
        findViewById<MaterialButton>(R.id.btn_sim_arterial).setOnClickListener {
            triggerSimulation("arterial_deteriorating", 7)
        }

        findViewById<FrameLayout>(R.id.btn_edit_profile_photo).setOnClickListener {
            showProfilePhotoOptions()
        }
        findViewById<MaterialButton>(R.id.btn_log_outcome_dialog).setOnClickListener {
            showLogOutcomeSheet()
        }
        findViewById<MaterialButton>(R.id.btn_load_samples).setOnClickListener {
            loadSampleCases()
        }
        findViewById<MaterialButton>(R.id.btn_clear_ward).setOnClickListener {
            confirmClearWard()
        }
        findViewById<LinearLayout>(R.id.btn_change_password).setOnClickListener {
            showChangePasswordSheet()
        }
        findViewById<LinearLayout>(R.id.row_connection_mode).setOnClickListener {
            showConnectionModeSheet()
        }
        findViewById<MaterialButton>(R.id.btn_logout).setOnClickListener {
            logoutToLogin()
        }
    }

    private fun populateProfileHeader() {
        tvProfileName.text = userName
        tvProfileRole.text = userRole
        tvProfileEmail.text = userEmail.ifBlank { "Signed-in clinician" }

        val parts = userName.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
        val initials = when {
            parts.size >= 2 -> "${parts[0].first()}${parts[1].first()}"
            parts.size == 1 -> parts[0].take(2)
            else -> "FG"
        }.uppercase(Locale.US)
        tvProfileInitials.text = initials

        val bmp = ImageUtils.decodeDataUriBitmap(userPhotoDataUri)
        if (bmp != null) {
            ivProfilePhoto.setImageBitmap(bmp)
            ivProfilePhoto.visibility = View.VISIBLE
            tvProfileInitials.visibility = View.GONE
        } else {
            ivProfilePhoto.setImageDrawable(null)
            ivProfilePhoto.visibility = View.GONE
            tvProfileInitials.visibility = View.VISIBLE
        }
    }

    private fun updateServerModeLabel() {
        tvActiveServerLabel.text = if (ApiClient.isLocalUsbMode(this)) {
            "Local USB Viva Mode (127.0.0.1:5000)"
        } else {
            "Live Cloud (flap-guard-ai.onrender.com)"
        }
    }

    private fun showNotice(msg: String) {
        Snackbar.make(rootView, msg, Snackbar.LENGTH_SHORT)
            .setAnchorView(bottomNav)
            .show()
    }

    private fun syncAll() {
        fetchPatients()
        fetchAlerts(silent = false)
        fetchProfileStats()
    }

    private fun handleUnauthorized(statusCode: Int): Boolean {
        if (statusCode == 401) {
            logoutToLogin()
            return true
        }
        return false
    }

    private fun logoutToLogin() {
        getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE).edit()
            .remove("auth_token")
            .remove("user_photo")
            .apply()
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    private fun fetchPatients() {
        beginSync()
        executor.execute {
            val res = ApiClient.request(serverUrl(), "/mobile/patients", "GET", null, authToken)
            endSync()
            mainHandler.post {
                if (!res.ok) {
                    if (!handleUnauthorized(res.statusCode)) {
                        showNotice("Ward sync: ${res.errorMessage}")
                    }
                    return@post
                }
                val arr = res.json.optJSONArray("patients") ?: JSONArray()
                parseAndRenderPatients(arr)
            }
        }
    }

    private fun parseAndRenderPatients(arr: JSONArray) {
        val prevId = selectedPatient()?.id
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
        tvStatPatients.text = patientsList.size.toString()

        cardEmptyWard.visibility = if (patientsList.isEmpty()) View.VISIBLE else View.GONE
        rvPatients.visibility = if (patientsList.isEmpty()) View.GONE else View.VISIBLE
        patientAdapter.submitList(patientsList)

        val restoredIdx = if (prevId != null) patientsList.indexOfFirst { it.id == prevId } else -1
        selectedPatientIndex = if (restoredIdx >= 0) restoredIdx else 0
        refreshSelectedPatientLabel()
    }

    private fun selectedPatient(): PatientItem? {
        return if (selectedPatientIndex in patientsList.indices) patientsList[selectedPatientIndex] else null
    }

    private fun refreshSelectedPatientLabel() {
        val p = selectedPatient()
        tvSelectedPatient.text = if (p != null) {
            "${p.fullName} (${p.mrn})  •  ${p.riskTier}"
        } else {
            "No patients in ward yet"
        }
    }

    private fun showPatientPickerDialog() {
        if (patientsList.isEmpty()) {
            showNotice("Add a patient in the Ward tab first.")
            return
        }
        val labels = patientsList.map { "${it.fullName} (${it.mrn}) — ${it.riskTier}" }.toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle("Select Patient")
            .setSingleChoiceItems(labels, selectedPatientIndex) { dialog, which ->
                selectedPatientIndex = which
                refreshSelectedPatientLabel()
                dialog.dismiss()
            }
            .show()
    }

    private fun openAssessForPatient(patient: PatientItem) {
        val idx = patientsList.indexOfFirst { it.id == patient.id }
        if (idx >= 0) {
            selectedPatientIndex = idx
            refreshSelectedPatientLabel()
        }
        bottomNav.selectedItemId = R.id.nav_assess
    }

    private fun showPatientDetailSheet(patient: PatientItem) {
        val sheet = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.sheet_patient_detail, null)
        sheet.setContentView(view)
        sheet.behavior.state = BottomSheetBehavior.STATE_EXPANDED
        sheet.behavior.skipCollapsed = true

        view.findViewById<TextView>(R.id.tv_sheet_patient_name).text = patient.fullName
        view.findViewById<TextView>(R.id.tv_sheet_patient_meta).text = String.format(
            Locale.US,
            "%s  •  %dy  •  %.1fh post-op",
            patient.mrn, patient.age, patient.hoursPostOp
        )
        view.findViewById<TextView>(R.id.tv_sheet_flap_info).text =
            "${patient.flapType}  —  ${patient.surgerySite}"

        val badge = view.findViewById<TextView>(R.id.tv_sheet_risk_badge)
        badge.text = patient.riskTier.uppercase(Locale.US)
        val (bg, fg) = when (patient.riskTier) {
            "Critical" -> "#FEE2E2" to "#B91C1C"
            "High" -> "#FEF3C7" to "#B45309"
            "Moderate" -> "#DBEAFE" to "#1D4ED8"
            "Low" -> "#DCFCE7" to "#15803D"
            else -> "#F1F5F9" to "#475569"
        }
        badge.backgroundTintList = ColorStateList.valueOf(Color.parseColor(bg))
        badge.setTextColor(Color.parseColor(fg))

        view.findViewById<TextView>(R.id.tv_detail_delta).text =
            patient.tempDeltaC?.let { String.format(Locale.US, "%.2f °C", it) } ?: "--"
        view.findViewById<TextView>(R.id.tv_detail_spo2).text =
            patient.spo2Pct?.let { String.format(Locale.US, "%.1f%%", it) } ?: "--"
        view.findViewById<TextView>(R.id.tv_detail_doppler).text =
            patient.dopplerCmS?.let { String.format(Locale.US, "%.1f cm/s", it) } ?: "--"
        view.findViewById<TextView>(R.id.tv_detail_visual).text =
            patient.visualScore?.let { String.format(Locale.US, "%.0f / 100", it) } ?: "--"
        view.findViewById<TextView>(R.id.tv_detail_summary).text =
            patient.alertMessage.ifBlank { "No sensor assessments logged yet for this patient." }

        view.findViewById<MaterialButton>(R.id.btn_sheet_assess).setOnClickListener {
            sheet.dismiss()
            openAssessForPatient(patient)
        }
        view.findViewById<MaterialButton>(R.id.btn_sheet_remove).setOnClickListener {
            sheet.dismiss()
            confirmDeletePatient(patient)
        }
        sheet.show()
    }

    private fun showAddPatientSheet() {
        val sheet = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.sheet_add_patient, null)
        sheet.setContentView(view)
        sheet.behavior.state = BottomSheetBehavior.STATE_EXPANDED
        sheet.behavior.skipCollapsed = true

        val tilMrn = view.findViewById<TextInputLayout>(R.id.til_add_mrn)
        val tilName = view.findViewById<TextInputLayout>(R.id.til_add_name)
        val tilAge = view.findViewById<TextInputLayout>(R.id.til_add_age)
        val tilHours = view.findViewById<TextInputLayout>(R.id.til_add_hours)
        val tilSite = view.findViewById<TextInputLayout>(R.id.til_add_site)

        val etMrn = view.findViewById<TextInputEditText>(R.id.et_add_mrn)
        val etName = view.findViewById<TextInputEditText>(R.id.et_add_name)
        val etAge = view.findViewById<TextInputEditText>(R.id.et_add_age)
        val etHours = view.findViewById<TextInputEditText>(R.id.et_add_hours)
        val etSite = view.findViewById<TextInputEditText>(R.id.et_add_site)
        val chipGroup = view.findViewById<ChipGroup>(R.id.chip_group_flap_type)
        val btnSave = view.findViewById<MaterialButton>(R.id.btn_confirm_add_patient)

        btnSave.setOnClickListener {
            listOf(tilMrn, tilName, tilAge, tilHours, tilSite).forEach { it.error = null }

            val mrn = etMrn.text?.toString()?.trim()?.uppercase(Locale.US).orEmpty()
            val name = etName.text?.toString()?.trim().orEmpty()
            val age = etAge.text?.toString()?.trim()?.toIntOrNull()
            val hours = etHours.text?.toString()?.trim()?.toDoubleOrNull()
            val site = etSite.text?.toString()?.trim().orEmpty()
            val flapType = view.findViewById<Chip>(chipGroup.checkedChipId)?.text?.toString()
                ?: "Radial Forearm Free Flap"

            var valid = true
            if (mrn.isBlank()) {
                tilMrn.error = "MRN is required"
                valid = false
            }
            if (name.length < 2) {
                tilName.error = "Enter patient full name"
                valid = false
            }
            if (age == null || age !in 1..120) {
                tilAge.error = "1–120"
                valid = false
            }
            if (hours == null || hours < 0.0 || hours > 720.0) {
                tilHours.error = "0–720"
                valid = false
            }
            if (site.length < 2) {
                tilSite.error = "Enter surgical site"
                valid = false
            }
            if (!valid) return@setOnClickListener

            btnSave.isEnabled = false
            val payload = JSONObject().apply {
                put("mrn", mrn)
                put("full_name", name)
                put("age", age!!)
                put("flap_type", flapType)
                put("surgery_site", site)
                put("hours_post_op", hours!!)
            }

            beginSync()
            executor.execute {
                val res = ApiClient.request(serverUrl(), "/mobile/patients", "POST", payload, authToken)
                endSync()
                mainHandler.post {
                    btnSave.isEnabled = true
                    if (res.ok) {
                        sheet.dismiss()
                        showNotice("Enrolled $name ($mrn).")
                        fetchPatients()
                    } else {
                        tilMrn.error = res.errorMessage
                    }
                }
            }
        }
        sheet.show()
    }

    private fun confirmDeletePatient(patient: PatientItem) {
        MaterialAlertDialogBuilder(this)
            .setTitle("Remove Patient?")
            .setMessage("Remove ${patient.fullName} (${patient.mrn}) and all associated readings from ward monitoring?")
            .setPositiveButton("Remove") { _, _ ->
                beginSync()
                executor.execute {
                    val res = ApiClient.request(
                        serverUrl(),
                        "/mobile/patients/${patient.id}/delete",
                        "POST",
                        JSONObject(),
                        authToken
                    )
                    endSync()
                    mainHandler.post {
                        if (res.ok) {
                            showNotice("Removed ${patient.fullName}.")
                            syncAll()
                        } else {
                            showNotice("Remove failed: ${res.errorMessage}")
                        }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun createTempCameraUri(prefix: String): Uri {
        val dir = File(cacheDir, "camera").apply { mkdirs() }
        val file = File(dir, "${prefix}_${System.currentTimeMillis()}.jpg")
        return FileProvider.getUriForFile(this, "${packageName}.fileprovider", file)
    }

    private fun launchFlapCamera() {
        try {
            val uri = createTempCameraUri("flap")
            pendingFlapCameraUri = uri
            flapCameraLauncher.launch(uri)
        } catch (e: Exception) {
            showNotice("Camera unavailable: ${e.message}")
        }
    }

    private fun analyzeFlapImageUri(uri: Uri) {
        tvVisionResult.text = "Analyzing centered 50% flap ROI via CIE-LAB OpenCV..."
        beginSync()
        executor.execute {
            try {
                val scaled = ImageUtils.decodeScaledRotatedBitmap(this, uri, 640)
                if (scaled == null) {
                    endSync()
                    mainHandler.post { tvVisionResult.text = "Could not decode selected image." }
                    return@execute
                }
                val encoded = ImageUtils.toBase64Jpeg(scaled, 88)
                mainHandler.post {
                    ivFlapPreview.setImageBitmap(scaled)
                    layoutPreviewFrame.visibility = View.VISIBLE
                }

                val body = JSONObject().put("image_base64", encoded)
                val res = ApiClient.request(serverUrl(), "/mobile/analyze-image", "POST", body, authToken)
                endSync()
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
                            "\nCapture Note: ${warnings.optString(0)}"
                        } else {
                            ""
                        }

                        tvVisionResult.text = String.format(
                            Locale.US,
                            "Triage: %s\nVisual Perfusion: %.1f / 100  •  Quality: %s\nRedness %.3f  •  Cyanosis %.3f  •  Pallor %.3f%s",
                            triage, score, qStatus, scannedRedness, scannedCyanosis, scannedPallor, warnText
                        )
                    } else {
                        tvVisionResult.text = "Image analysis failed: ${res.errorMessage}"
                    }
                }
            } catch (e: Exception) {
                endSync()
                mainHandler.post { tvVisionResult.text = "Image error: ${e.message}" }
            }
        }
    }

    private fun submitSensorReading() {
        val patient = selectedPatient()
        if (patient == null) {
            showNotice("Add a patient in the Ward tab first.")
            return
        }

        listOf(tilIrTemp, tilCtrlTemp, tilSpo2, tilDoppler).forEach { it.error = null }

        val irTemp = etIrTemp.text?.toString()?.trim()?.toDoubleOrNull()
        val ctrlTemp = etCtrlTemp.text?.toString()?.trim()?.toDoubleOrNull()
        val spo2 = etSpo2.text?.toString()?.trim()?.toDoubleOrNull()
        val doppler = etDoppler.text?.toString()?.trim()?.toDoubleOrNull()
        val hours = etHours.text?.toString()?.trim()?.toDoubleOrNull() ?: patient.hoursPostOp

        var ok = true
        if (irTemp == null) {
            tilIrTemp.error = "Required"
            ok = false
        }
        if (ctrlTemp == null) {
            tilCtrlTemp.error = "Required"
            ok = false
        }
        if (spo2 == null) {
            tilSpo2.error = "Required"
            ok = false
        }
        if (doppler == null) {
            tilDoppler.error = "Required"
            ok = false
        }
        if (!ok) return

        beginSync()
        executor.execute {
            val payload = JSONObject().apply {
                put("ir_flap_temp_c", irTemp!!)
                put("control_skin_temp_c", ctrlTemp!!)
                put("spo2_pct", spo2!!)
                put("doppler_flow_cm_s", doppler!!)
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
            endSync()
            mainHandler.post {
                if (res.ok) {
                    showPredictionCard(res.json.getJSONObject("reading"))
                    fetchPatients()
                    fetchAlerts(silent = false)
                } else {
                    showNotice("Assessment failed: ${res.errorMessage}")
                }
            }
        }
    }

    private fun triggerSimulation(scenario: String, step: Int) {
        val patient = selectedPatient()
        if (patient == null) {
            showNotice("Add a patient in the Ward tab first.")
            return
        }
        beginSync()
        executor.execute {
            val payload = JSONObject().put("scenario", scenario).put("step", step)
            val res = ApiClient.request(
                serverUrl(),
                "/mobile/patients/${patient.id}/simulate",
                "POST",
                payload,
                authToken
            )
            endSync()
            mainHandler.post {
                if (res.ok) {
                    showPredictionCard(res.json.getJSONObject("reading"))
                    fetchPatients()
                    fetchAlerts(silent = false)
                } else {
                    showNotice("Simulation failed: ${res.errorMessage}")
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
        if (!silent) beginSync()
        executor.execute {
            val res = ApiClient.request(serverUrl(), "/mobile/alerts", "GET", null, authToken)
            alertPollInFlight.set(false)
            if (!silent) endSync()
            mainHandler.post {
                if (!res.ok) {
                    if (!silent && !handleUnauthorized(res.statusCode)) {
                        showNotice("Alert sync: ${res.errorMessage}")
                    }
                    return@post
                }
                val unack = res.json.optInt("unacknowledged_count", 0)
                val alertsArr = res.json.optJSONArray("alerts") ?: JSONArray()
                tvStatAlerts.text = unack.toString()

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
        beginSync()
        executor.execute {
            val res = ApiClient.request(
                serverUrl(),
                "/mobile/alerts/$readingId/acknowledge",
                "POST",
                JSONObject(),
                authToken
            )
            endSync()
            mainHandler.post {
                if (res.ok) {
                    showNotice("Alert #$readingId acknowledged.")
                    lastAlertsSignature = ""
                    fetchAlerts(silent = false)
                } else {
                    showNotice("Acknowledge failed: ${res.errorMessage}")
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
                    val rawPhoto = userObj.optString("profile_photo", "")
                    userPhotoDataUri = if (rawPhoto == "null") "" else rawPhoto
                    getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
                        .edit()
                        .putString("user_photo", userPhotoDataUri)
                        .apply()
                    populateProfileHeader()
                }
                tvStatModel.text = res.json.optString("model_version", "v1.0")
                tvStatPatients.text = res.json.optInt("patients_count", 0).toString()
                tvStatAlerts.text = res.json.optInt("unacknowledged_alerts", 0).toString()
            }
        }
    }

    private fun showProfilePhotoOptions() {
        val items = if (userPhotoDataUri.isNotBlank()) {
            arrayOf("Choose from Gallery", "Take Photo", "Remove Photo")
        } else {
            arrayOf("Choose from Gallery", "Take Photo")
        }
        MaterialAlertDialogBuilder(this)
            .setTitle("Profile Photo")
            .setItems(items) { _, which ->
                when (which) {
                    0 -> profileGalleryLauncher.launch("image/*")
                    1 -> {
                        try {
                            val uri = createTempCameraUri("avatar")
                            pendingProfileCameraUri = uri
                            profileCameraLauncher.launch(uri)
                        } catch (e: Exception) {
                            showNotice("Camera unavailable: ${e.message}")
                        }
                    }
                    2 -> removeProfilePhoto()
                }
            }
            .show()
    }

    private fun uploadProfilePhotoUri(uri: Uri) {
        beginSync()
        executor.execute {
            try {
                val bmp = ImageUtils.decodeScaledRotatedBitmap(this, uri, 320)
                if (bmp == null) {
                    endSync()
                    mainHandler.post { showNotice("Could not load selected photo.") }
                    return@execute
                }
                val b64 = ImageUtils.toBase64Jpeg(bmp, 85)
                val res = ApiClient.request(
                    serverUrl(),
                    "/mobile/profile/photo",
                    "POST",
                    JSONObject().put("image_base64", b64),
                    authToken
                )
                endSync()
                mainHandler.post {
                    if (res.ok) {
                        val photo = res.json.optJSONObject("user")?.optString("profile_photo", "").orEmpty()
                        userPhotoDataUri = if (photo == "null") "" else photo
                        getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
                            .edit()
                            .putString("user_photo", userPhotoDataUri)
                            .apply()
                        populateProfileHeader()
                        showNotice("Profile photo updated.")
                    } else {
                        showNotice("Photo upload failed: ${res.errorMessage}")
                    }
                }
            } catch (e: Exception) {
                endSync()
                mainHandler.post { showNotice("Photo error: ${e.message}") }
            }
        }
    }

    private fun removeProfilePhoto() {
        beginSync()
        executor.execute {
            val res = ApiClient.request(
                serverUrl(),
                "/mobile/profile/photo",
                "POST",
                JSONObject().put("remove", true),
                authToken
            )
            endSync()
            mainHandler.post {
                if (res.ok) {
                    userPhotoDataUri = ""
                    getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
                        .edit()
                        .remove("user_photo")
                        .apply()
                    populateProfileHeader()
                    showNotice("Profile photo removed.")
                } else {
                    showNotice("Failed: ${res.errorMessage}")
                }
            }
        }
    }

    private fun showLogOutcomeSheet() {
        if (patientsList.isEmpty()) {
            showNotice("Add a patient in the Ward tab first.")
            return
        }
        val sheet = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.sheet_log_outcome, null)
        sheet.setContentView(view)
        sheet.behavior.state = BottomSheetBehavior.STATE_EXPANDED
        sheet.behavior.skipCollapsed = true

        val spPatient = view.findViewById<Spinner>(R.id.spinner_outcome_patient_sheet)
        spPatient.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            patientsList.map { "${it.fullName} (${it.mrn})" }
        )
        val chipGroup = view.findViewById<ChipGroup>(R.id.chip_group_outcome)
        val etNotes = view.findViewById<TextInputEditText>(R.id.et_outcome_notes_sheet)
        val btnSave = view.findViewById<MaterialButton>(R.id.btn_save_outcome_sheet)

        btnSave.setOnClickListener {
            val patient = patientsList[spPatient.selectedItemPosition]
            val outcome = view.findViewById<Chip>(chipGroup.checkedChipId)?.text?.toString() ?: "Normal"
            val notes = etNotes.text?.toString()?.trim().orEmpty()

            btnSave.isEnabled = false
            beginSync()
            executor.execute {
                val body = JSONObject().apply {
                    put("patient_id", patient.id)
                    put("verified_outcome", outcome)
                    put("surgeon_notes", notes)
                }
                val res = ApiClient.request(serverUrl(), "/mobile/outcomes", "POST", body, authToken)
                endSync()
                mainHandler.post {
                    btnSave.isEnabled = true
                    if (res.ok) {
                        sheet.dismiss()
                        showNotice("Verified outcome saved for continuous learning.")
                    } else {
                        showNotice("Failed: ${res.errorMessage}")
                    }
                }
            }
        }
        sheet.show()
    }

    private fun showChangePasswordSheet() {
        val sheet = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.sheet_change_password, null)
        sheet.setContentView(view)
        sheet.behavior.state = BottomSheetBehavior.STATE_EXPANDED
        sheet.behavior.skipCollapsed = true

        val tilCurr = view.findViewById<TextInputLayout>(R.id.til_curr_pw)
        val tilNew = view.findViewById<TextInputLayout>(R.id.til_new_pw)
        val etCurr = view.findViewById<TextInputEditText>(R.id.et_curr_pw)
        val etNew = view.findViewById<TextInputEditText>(R.id.et_new_pw)
        val btnUpdate = view.findViewById<MaterialButton>(R.id.btn_confirm_change_pw)

        btnUpdate.setOnClickListener {
            tilCurr.error = null
            tilNew.error = null
            val curr = etCurr.text?.toString().orEmpty()
            val next = etNew.text?.toString().orEmpty()
            if (curr.isBlank()) {
                tilCurr.error = "Enter current password"
                return@setOnClickListener
            }
            if (next.length < 8) {
                tilNew.error = "Minimum 8 characters"
                return@setOnClickListener
            }

            btnUpdate.isEnabled = false
            beginSync()
            executor.execute {
                val body = JSONObject()
                    .put("current_password", curr)
                    .put("new_password", next)
                val res = ApiClient.request(serverUrl(), "/mobile/profile/password", "POST", body, authToken)
                endSync()
                mainHandler.post {
                    btnUpdate.isEnabled = true
                    if (res.ok) {
                        sheet.dismiss()
                        showNotice("Password updated.")
                    } else {
                        tilCurr.error = res.errorMessage
                    }
                }
            }
        }
        sheet.show()
    }

    private fun showConnectionModeSheet() {
        val sheet = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.sheet_connection_mode, null)
        sheet.setContentView(view)
        sheet.behavior.state = BottomSheetBehavior.STATE_EXPANDED
        sheet.behavior.skipCollapsed = true

        val cloudCard = view.findViewById<MaterialCardView>(R.id.option_live_cloud)
        val usbCard = view.findViewById<MaterialCardView>(R.id.option_local_usb)

        val usb = ApiClient.isLocalUsbMode(this)
        val activeColor = Color.parseColor("#0F4C81")
        val idleColor = Color.parseColor("#E2E8F0")
        cloudCard.strokeColor = if (!usb) activeColor else idleColor
        usbCard.strokeColor = if (usb) activeColor else idleColor

        cloudCard.setOnClickListener {
            ApiClient.setLocalUsbMode(this, false)
            updateServerModeLabel()
            sheet.dismiss()
            showNotice("Switched to Live Cloud")
            syncAll()
        }
        usbCard.setOnClickListener {
            ApiClient.setLocalUsbMode(this, true)
            updateServerModeLabel()
            sheet.dismiss()
            showNotice("Switched to Local USB Viva Mode")
            syncAll()
        }
        sheet.show()
    }

    private fun loadSampleCases() {
        beginSync()
        executor.execute {
            val res = ApiClient.request(serverUrl(), "/mobile/ward/load-samples", "POST", JSONObject(), authToken)
            endSync()
            mainHandler.post {
                if (res.ok) {
                    val created = res.json.optInt("created_count", 0)
                    showNotice(if (created > 0) "Loaded $created sample OMFS cases." else "Sample cases already in ward.")
                    syncAll()
                    bottomNav.selectedItemId = R.id.nav_ward
                } else {
                    showNotice("Failed: ${res.errorMessage}")
                }
            }
        }
    }

    private fun confirmClearWard() {
        MaterialAlertDialogBuilder(this)
            .setTitle("Clear Entire Ward?")
            .setMessage("This removes all patient records, readings, and alerts from the active database.")
            .setPositiveButton("Clear Ward") { _, _ ->
                beginSync()
                executor.execute {
                    val res = ApiClient.request(serverUrl(), "/mobile/ward/clear", "POST", JSONObject(), authToken)
                    endSync()
                    mainHandler.post {
                        if (res.ok) {
                            showNotice("Ward cleared.")
                            lastAlertsSignature = ""
                            syncAll()
                            bottomNav.selectedItemId = R.id.nav_ward
                        } else {
                            showNotice("Failed: ${res.errorMessage}")
                        }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
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
