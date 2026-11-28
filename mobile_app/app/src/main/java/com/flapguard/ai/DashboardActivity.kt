package com.flapguard.ai

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.graphics.Typeface
import android.graphics.Paint
import android.graphics.Canvas
import android.graphics.Bitmap
import android.content.IntentFilter
import android.content.BroadcastReceiver
import android.app.PendingIntent
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.EditText
import android.text.TextWatcher
import android.text.Editable
import android.os.Handler
import android.os.Looper
import android.app.KeyguardManager
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.CancellationSignal
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
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
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
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
    private val currentAlerts = mutableListOf<AlertItem>()
    private var selectedPatientIndex: Int = 0
    private var unackCount: Int = 0
    private val notifiedAlertIds = mutableSetOf<Int>()
    private var lastAlertsSignature = ""
    private var pendingFlapCameraUri: Uri? = null
    private var pendingProfileCameraUri: Uri? = null

    private lateinit var offlineQueueDb: OfflineQueueDb
    private var controlSkinBase64: String? = null
    private lateinit var cardOfflineQueue: MaterialCardView
    private lateinit var tvOfflineQueueText: TextView
    private lateinit var tvControlSkinStatus: TextView
    private lateinit var btnPickControlSkin: MaterialButton
    private lateinit var switchBgMonitor: MaterialSwitch
    private lateinit var switchScreenPrivacy: MaterialSwitch
    private lateinit var switchAppLock: MaterialSwitch
    private var appLockOverlay: LinearLayout? = null
    private var isAppLocked = false
    private var needsReauthOnReturn = false
    private var suppressNextAppLock = false
    private var pendingEnableAppLock = false
    private var ignoreAppLockSwitchChange = false

    private val deviceCredentialLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        suppressNextAppLock = false
        if (result.resultCode == RESULT_OK) {
            onAppUnlocked()
        } else {
            onAppUnlockFailedOrCancelled(null)
        }
    }

    private var scannedRedness = 0.14
    private var scannedCyanosis = 0.03
    private var scannedPallor = 0.42

    private lateinit var rootView: View
    private lateinit var tvToolbarTitle: TextView
    private lateinit var btnAckAll: MaterialButton
    private lateinit var btnWardHandoff: MaterialButton
    private lateinit var layoutExamFields: LinearLayout
    private lateinit var tvToggleExamLabel: TextView
    private lateinit var cgCapRefill: ChipGroup
    private lateinit var cgTurgor: ChipGroup
    private lateinit var cgPinprick: ChipGroup
    private lateinit var cgDopplerQual: ChipGroup
    private lateinit var btnRefreshAll: ImageButton
    private lateinit var progressSync: ProgressBar
    private lateinit var bottomNav: BottomNavigationView

    // Tab containers
    private lateinit var sectionWard: FrameLayout
    private lateinit var sectionAssess: ScrollView
    private lateinit var sectionAlerts: LinearLayout
    private lateinit var sectionProfile: ScrollView

    // Ward views
    private lateinit var cardWardAlertPill: MaterialCardView
    private lateinit var tvWardAlertText: TextView
    private lateinit var tvKpiCritical: TextView
    private lateinit var tvKpiHigh: TextView
    private lateinit var tvKpiTotal: TextView
    private lateinit var cardEmptyWard: MaterialCardView
    private lateinit var rvPatients: RecyclerView
    private lateinit var patientAdapter: PatientAdapter
    private lateinit var etWardSearch: EditText
    private lateinit var btnClearWardSearch: ImageButton
    private lateinit var tvWardFilterEmpty: TextView
    private lateinit var chipFilterAll: TextView
    private lateinit var chipFilterCritical: TextView
    private lateinit var chipFilterOverdue: TextView
    private lateinit var chipFilterHealthy: TextView
    private var wardSearchQuery: String = ""
    private var wardFilterMode: String = "ALL"

    private val patientChartLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK && result.data != null) {
            val action = result.data!!.getStringExtra("action").orEmpty()
            val pid = result.data!!.getIntExtra("patient_id", -1)
            val match = patientsList.firstOrNull { it.id == pid } ?: return@registerForActivityResult
            when (action) {
                "assess" -> openAssessForPatient(match)
                "archive" -> confirmDeletePatient(match)
                "outcome" -> {
                    val idx = patientsList.indexOfFirst { it.id == match.id }
                    if (idx >= 0) {
                        selectedPatientIndex = idx
                        refreshSelectedPatientLabel()
                    }
                    showLogOutcomeSheet()
                }
            }
        }
    }

    private val sbarHandoffLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK && result.data != null) {
            val pid = result.data!!.getIntExtra("open_patient_id", -1)
            val match = patientsList.firstOrNull { it.id == pid }
            if (match != null) {
                showPatientDetailSheet(match)
            }
        }
    }

    // Assess views
    private lateinit var cardSelectPatient: MaterialCardView
    private lateinit var tvSelectedAvatar: TextView
    private lateinit var tvSelectedPatient: TextView
    private lateinit var layoutPreviewFrame: FrameLayout
    private lateinit var ivFlapPreview: ImageView
    private lateinit var tvVisionResult: TextView
    private lateinit var tvSelectedPatientSub: TextView
    private lateinit var tileIrTemp: LinearLayout
    private lateinit var tileCtrlTemp: LinearLayout
    private lateinit var tileSpo2: LinearLayout
    private lateinit var tileDoppler: LinearLayout
    private lateinit var tvHintIrTemp: TextView
    private lateinit var tvHintCtrlTemp: TextView
    private lateinit var tvHintSpo2: TextView
    private lateinit var tvHintDoppler: TextView
    private lateinit var etIrTemp: EditText
    private lateinit var etCtrlTemp: EditText
    private lateinit var etSpo2: EditText
    private lateinit var etDoppler: EditText
    private lateinit var etHours: EditText
    private lateinit var tabAlertsActive: TextView
    private lateinit var tabAlertsHistory: TextView
    private lateinit var tvEmptyAlertsText: TextView
    private lateinit var tvActiveThemeLabel: TextView
    private var alertTabMode: String = "ACTIVE"
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

    private var receiverRegistered = false
    private val alertsUpdatedReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            lastAlertsSignature = ""
            fetchAlerts(silent = false)
            fetchPatients()
        }
    }

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
        if (success && uri != null) analyzeFlapImageUri(uri)
    }

    private val flapGalleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri != null) analyzeFlapImageUri(uri)
    }

    private val profileCameraLauncher = registerForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { success: Boolean ->
        val uri = pendingProfileCameraUri
        if (success && uri != null) uploadProfilePhotoUri(uri)
    }

    private val controlSkinGalleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri != null) loadControlSkinReference(uri)
    }

    private val profileGalleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri != null) uploadProfilePhotoUri(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        SecurePrefs.applySavedTheme(this)
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_dashboard)

        val prefs = getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
        authToken = SecurePrefs.getAuthToken(this)
        userName = prefs.getString("user_name", "Clinician") ?: "Clinician"
        userRole = prefs.getString("user_role", "Surgeon") ?: "Surgeon"
        userEmail = prefs.getString("user_email", "") ?: ""
        userPhotoDataUri = prefs.getString("user_photo", "") ?: ""

        if (authToken.isBlank()) {
            logoutToLogin()
            return
        }

        offlineQueueDb = OfflineQueueDb(this)
        applyScreenPrivacyFlag()
        // App lock triggers once window is attached below
        NotificationHelper.ensureChannels(this)
        AlertJobService.schedule(this)
        if (AlertMonitorService.isEnabled(this)) AlertMonitorService.start(this)
        requestNotificationPermissionIfNeeded()
        bindViews()
        setupAdapters()
        setupNavigation()
        setupActions()
        populateProfileHeader()
        updateServerModeLabel()
        if (SecurePrefs.isAppLockEnabled(this)) {
            showAppLockOverlay()
            window.decorView.post { promptDeviceUnlock() }
        }

        ContextCompat.registerReceiver(
            this,
            alertsUpdatedReceiver,
            IntentFilter(AlertActionReceiver.ACTION_ALERTS_UPDATED),
            ContextCompat.RECEIVER_NOT_EXPORTED
        )
        receiverRegistered = true

        syncAll()
        handleNotificationIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleNotificationIntent(intent)
    }

    private fun handleNotificationIntent(intent: Intent?) {
        if (intent?.getStringExtra("open_tab") == "alerts") {
            intent.removeExtra("open_tab")
            bottomNav.selectedItemId = R.id.nav_alerts
            lastAlertsSignature = ""
            fetchAlerts(silent = false)
        }
    }

    override fun onResume() {
        super.onResume()
        mainHandler.postDelayed(pollRunnable, 15_000L)
    }

    override fun onPause() {
        super.onPause()
        mainHandler.removeCallbacks(pollRunnable)
    }

    override fun onStart() {
        super.onStart()
        if (suppressNextAppLock) {
            suppressNextAppLock = false
            return
        }
        if (SecurePrefs.isAppLockEnabled(this) && (needsReauthOnReturn || isAppLocked)) {
            showAppLockOverlay()
            window.decorView.post { promptDeviceUnlock() }
        }
    }

    override fun onStop() {
        super.onStop()
        if (SecurePrefs.isAppLockEnabled(this) && !suppressNextAppLock) {
            needsReauthOnReturn = true
            showAppLockOverlay()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        mainHandler.removeCallbacks(pollRunnable)
        if (receiverRegistered) {
            try {
                unregisterReceiver(alertsUpdatedReceiver)
            } catch (_: Exception) {
            }
            receiverRegistered = false
        }
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
        btnAckAll = findViewById(R.id.btn_ack_all)
        btnWardHandoff = findViewById(R.id.btn_ward_handoff)
        layoutExamFields = findViewById(R.id.layout_exam_fields)
        tvToggleExamLabel = findViewById(R.id.tv_toggle_exam_label)
        cgCapRefill = findViewById(R.id.cg_cap_refill)
        cgTurgor = findViewById(R.id.cg_turgor)
        cgPinprick = findViewById(R.id.cg_pinprick)
        cgDopplerQual = findViewById(R.id.cg_doppler_qual)
        btnRefreshAll = findViewById(R.id.btn_refresh_all)
        progressSync = findViewById(R.id.progress_sync)
        bottomNav = findViewById(R.id.bottom_nav)

        sectionWard = findViewById(R.id.section_ward)
        sectionAssess = findViewById(R.id.section_assess)
        sectionAlerts = findViewById(R.id.section_alerts)
        sectionProfile = findViewById(R.id.section_profile)

        cardOfflineQueue = findViewById(R.id.card_offline_queue)
        tvOfflineQueueText = findViewById(R.id.tv_offline_queue_text)
        tvControlSkinStatus = findViewById(R.id.tv_control_skin_status)
        btnPickControlSkin = findViewById(R.id.btn_pick_control_skin)
        switchBgMonitor = findViewById(R.id.switch_bg_monitor)
        switchScreenPrivacy = findViewById(R.id.switch_screen_privacy)
        switchAppLock = findViewById(R.id.switch_app_lock)
        cardWardAlertPill = findViewById(R.id.card_ward_alert_pill)
        tvWardAlertText = findViewById(R.id.tv_ward_alert_text)
        tvKpiCritical = findViewById(R.id.tv_kpi_critical)
        tvKpiHigh = findViewById(R.id.tv_kpi_high)
        tvKpiTotal = findViewById(R.id.tv_kpi_total)
        cardEmptyWard = findViewById(R.id.card_empty_ward)
        rvPatients = findViewById(R.id.rv_patients)
        etWardSearch = findViewById(R.id.et_ward_search)
        btnClearWardSearch = findViewById(R.id.btn_clear_ward_search)
        tvWardFilterEmpty = findViewById(R.id.tv_ward_filter_empty)
        chipFilterAll = findViewById(R.id.chip_filter_all)
        chipFilterCritical = findViewById(R.id.chip_filter_critical)
        chipFilterOverdue = findViewById(R.id.chip_filter_overdue)
        chipFilterHealthy = findViewById(R.id.chip_filter_healthy)

        cardSelectPatient = findViewById(R.id.card_select_patient)
        tvSelectedAvatar = findViewById(R.id.tv_selected_avatar)
        tvSelectedPatient = findViewById(R.id.tv_selected_patient)
        layoutPreviewFrame = findViewById(R.id.layout_preview_frame)
        ivFlapPreview = findViewById(R.id.iv_flap_preview)
        tvVisionResult = findViewById(R.id.tv_vision_result)
        tvSelectedPatientSub = findViewById(R.id.tv_selected_patient_sub)
        tileIrTemp = findViewById(R.id.tile_ir_temp)
        tileCtrlTemp = findViewById(R.id.tile_ctrl_temp)
        tileSpo2 = findViewById(R.id.tile_spo2)
        tileDoppler = findViewById(R.id.tile_doppler)
        tvHintIrTemp = findViewById(R.id.tv_hint_ir_temp)
        tvHintCtrlTemp = findViewById(R.id.tv_hint_ctrl_temp)
        tvHintSpo2 = findViewById(R.id.tv_hint_spo2)
        tvHintDoppler = findViewById(R.id.tv_hint_doppler)
        tabAlertsActive = findViewById(R.id.tab_alerts_active)
        tabAlertsHistory = findViewById(R.id.tab_alerts_history)
        tvEmptyAlertsText = findViewById(R.id.tv_empty_alerts_text)
        tvActiveThemeLabel = findViewById(R.id.tv_active_theme_label)
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
            onAssessClick = { patient -> openAssessForPatient(patient) }
        )
        rvPatients.layoutManager = LinearLayoutManager(this)
        rvPatients.adapter = patientAdapter

        alertAdapter = AlertAdapter(
            onAcknowledge = { alert -> acknowledgeAlert(alert.id) },
            onCardClick = { alert -> openPatientFromAlert(alert) }
        )
        rvAlerts.layoutManager = LinearLayoutManager(this)
        rvAlerts.adapter = alertAdapter
    }

    private fun updateAckAllVisibility() {
        val onAlerts = bottomNav.selectedItemId == R.id.nav_alerts
        btnAckAll.visibility = if (onAlerts && unackCount > 0) View.VISIBLE else View.GONE
        val onWard = bottomNav.selectedItemId == R.id.nav_ward
        btnWardHandoff.visibility = if (onWard) View.VISIBLE else View.GONE
    }

    private fun setupNavigation() {
        bottomNav.setOnItemSelectedListener { item ->
            sectionWard.visibility = if (item.itemId == R.id.nav_ward) View.VISIBLE else View.GONE
            sectionAssess.visibility = if (item.itemId == R.id.nav_assess) View.VISIBLE else View.GONE
            sectionAlerts.visibility = if (item.itemId == R.id.nav_alerts) View.VISIBLE else View.GONE
            sectionProfile.visibility = if (item.itemId == R.id.nav_profile) View.VISIBLE else View.GONE

            when (item.itemId) {
                R.id.nav_ward -> {
                    tvToolbarTitle.text = "Ward"
                    fetchPatients()
                }
                R.id.nav_assess -> {
                    tvToolbarTitle.text = "Assess"
                    sectionAssess.post { sectionAssess.scrollTo(0, 0) }
                }
                R.id.nav_alerts -> {
                    tvToolbarTitle.text = "Alerts"
                    fetchAlerts(silent = false)
                }
                R.id.nav_profile -> {
                    tvToolbarTitle.text = "Profile"
                    sectionProfile.post { sectionProfile.scrollTo(0, 0) }
                    fetchProfileStats()
                }
            }
            mainHandler.post { updateAckAllVisibility() }
            true
        }
    }

    private fun setupActions() {
        btnRefreshAll.setOnClickListener { syncAll() }
        btnAckAll.setOnClickListener { acknowledgeAllAlerts() }

        cardWardAlertPill.setOnClickListener {
            bottomNav.selectedItemId = R.id.nav_alerts
        }

        findViewById<ExtendedFloatingActionButton>(R.id.fab_add_patient).setOnClickListener {
            showAddPatientSheet()
        }

        cardSelectPatient.setOnClickListener { showPatientPickerDialog() }

        findViewById<MaterialButton>(R.id.btn_open_camera).setOnClickListener { launchFlapCamera() }
        findViewById<MaterialButton>(R.id.btn_pick_gallery).setOnClickListener { suppressNextAppLock = true; flapGalleryLauncher.launch("image/*") }
        cardOfflineQueue.setOnClickListener { flushOfflineQueue(showFeedback = true) }
        btnPickControlSkin.setOnClickListener {
            if (controlSkinBase64 != null) {
                controlSkinBase64 = null
                tvControlSkinStatus.text = "Healthy skin photo: optional (balances room lighting)"
                btnPickControlSkin.text = "+ Add Skin"
                showNotice("Control skin reference cleared.")
            } else {
                suppressNextAppLock = true; controlSkinGalleryLauncher.launch("image/*")
            }
        }
        switchBgMonitor.isChecked = AlertMonitorService.isEnabled(this)
        switchBgMonitor.setOnCheckedChangeListener { _, isChecked ->
            AlertMonitorService.setEnabled(this, isChecked)
            showNotice(if (isChecked) "Alerts will keep checking while phone is locked." else "Lock-screen alert checks paused.")
        }
        switchScreenPrivacy.isChecked = SecurePrefs.isScreenPrivacyEnabled(this)
        switchScreenPrivacy.setOnCheckedChangeListener { _, isChecked ->
            SecurePrefs.setScreenPrivacyEnabled(this, isChecked)
            applyScreenPrivacyFlag()
            showNotice(if (isChecked) "Screenshots and app previews blocked." else "Screen privacy lock turned off.")
        }
        switchAppLock.isChecked = SecurePrefs.isAppLockEnabled(this)
        switchAppLock.setOnCheckedChangeListener { _, isChecked ->
            if (ignoreAppLockSwitchChange) return@setOnCheckedChangeListener
            if (isChecked) {
                if (!isDeviceLockAvailable()) {
                    ignoreAppLockSwitchChange = true
                    switchAppLock.isChecked = false
                    ignoreAppLockSwitchChange = false
                    showNotice("Set up a phone PIN, pattern, or fingerprint in Android Settings first.")
                    return@setOnCheckedChangeListener
                }
                pendingEnableAppLock = true
                promptDeviceUnlock()
            } else {
                pendingEnableAppLock = false
                SecurePrefs.setAppLockEnabled(this, false)
                hideAppLockOverlay()
                showNotice("App unlock lock turned off.")
            }
        }
        findViewById<LinearLayout>(R.id.row_sign_out_all).setOnClickListener {
            confirmSignOutOtherDevices()
        }
        btnWardHandoff.setOnClickListener { showSbarHandoffSheet() }
        setupWardTriageControls()
        setupPhase2Controls()
        findViewById<LinearLayout>(R.id.row_sbar_handoff).setOnClickListener { showSbarHandoffSheet() }
        findViewById<LinearLayout>(R.id.row_toggle_exam).setOnClickListener {
            val expand = layoutExamFields.visibility != View.VISIBLE
            layoutExamFields.visibility = if (expand) View.VISIBLE else View.GONE
            updateExamToggleBadge()
        }
        findViewById<LinearLayout>(R.id.row_delete_account).setOnClickListener {
            showDeleteAccountSheet()
        }
        findViewById<MaterialButton>(R.id.btn_submit_reading).setOnClickListener { submitSensorReading() }

        findViewById<MaterialCardView>(R.id.row_open_simulator).setOnClickListener {
            showSimulatorSheet()
        }

        findViewById<FrameLayout>(R.id.btn_edit_profile_photo).setOnClickListener {
            showProfilePhotoOptions()
        }
        findViewById<LinearLayout>(R.id.row_log_outcome).setOnClickListener {
            showLogOutcomeSheet()
        }
        findViewById<LinearLayout>(R.id.row_demo_data).setOnClickListener {
            showWardDataSheet()
        }
        findViewById<LinearLayout>(R.id.btn_change_password).setOnClickListener {
            showChangePasswordSheet()
        }
        findViewById<LinearLayout>(R.id.row_connection_mode).setOnClickListener {
            showConnectionModeSheet()
        }
        findViewById<MaterialCardView>(R.id.btn_logout).setOnClickListener {
            logoutToLogin()
        }
    }

    private fun initialsOf(name: String): String {
        val parts = name.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
        return when {
            parts.size >= 2 -> "${parts[0].first()}${parts[1].first()}"
            parts.size == 1 -> parts[0].take(2)
            else -> "FG"
        }.uppercase(Locale.US)
    }

    private fun populateProfileHeader() {
        tvProfileName.text = userName
        tvProfileRole.text = userRole
        tvProfileEmail.text = userEmail.ifBlank { "Signed-in clinician" }
        tvProfileInitials.text = initialsOf(userName)

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
            "Local USB Viva Mode"
        } else {
            "Live Cloud"
        }
    }

    private fun showNotice(msg: String) {
        Snackbar.make(rootView, msg, Snackbar.LENGTH_SHORT)
            .setAnchorView(bottomNav)
            .show()
    }

    private fun syncAll() {
        updateOfflineQueueBadge()
        flushOfflineQueue(showFeedback = false)
        fetchPatients()
        fetchAlerts(silent = false)
        fetchProfileStats()
    }

    private fun updateOfflineQueueBadge() {
        val pending = if (::offlineQueueDb.isInitialized) offlineQueueDb.count() else 0
        mainHandler.post {
            if (pending > 0) {
                cardOfflineQueue.visibility = View.VISIBLE
                tvOfflineQueueText.text = "$pending offline reading(s) queued — tap to sync now"
            } else {
                cardOfflineQueue.visibility = View.GONE
            }
        }
    }

    private fun flushOfflineQueue(showFeedback: Boolean) {
        if (!::offlineQueueDb.isInitialized || offlineQueueDb.count() == 0) return
        executor.execute {
            val (synced, remaining) = offlineQueueDb.flushAll(serverUrl(), authToken)
            updateOfflineQueueBadge()
            if (synced > 0) {
                mainHandler.post {
                    showNotice("Auto-synced $synced queued offline reading(s).")
                    fetchPatients()
                    fetchAlerts(silent = false)
                }
            } else if (showFeedback && remaining > 0) {
                mainHandler.post { showNotice("Still offline — $remaining reading(s) safely queued.") }
            }
        }
    }

    private fun loadControlSkinReference(uri: Uri) {
        executor.execute {
            val bmp = ImageUtils.decodeScaledRotatedBitmap(this, uri, 480)
            if (bmp == null) {
                mainHandler.post { showNotice("Could not load control skin photo.") }
                return@execute
            }
            controlSkinBase64 = ImageUtils.toBase64Jpeg(bmp, 85)
            mainHandler.post {
                tvControlSkinStatus.text = "Healthy skin photo attached · Lighting balanced"
                btnPickControlSkin.text = "Remove"
                showNotice("Control skin reference ready for paired scan.")
            }
        }
    }

    private fun handleUnauthorized(statusCode: Int): Boolean {
        if (statusCode == 401) {
            logoutToLogin()
            return true
        }
        return false
    }

    private fun logoutToLogin() {
        val tokenToRevoke = if (::authToken.isInitialized) authToken else SecurePrefs.getAuthToken(this)
        val urlToNotify = serverUrl()
        if (tokenToRevoke.isNotBlank()) {
            Thread {
                try {
                    ApiClient.request(urlToNotify, "/mobile/logout", "POST", JSONObject(), tokenToRevoke)
                } catch (_: Exception) {
                }
            }.start()
        }
        AlertMonitorService.setEnabled(this, false)
        AlertJobService.cancel(this)
        NotificationHelper.clearNotifiedHistory(this)
        SecurePrefs.clearAuthToken(this)
        getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE).edit()
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
                    alertMessage = latest?.optString("alert_message").orEmpty(),
                    trendStatus = latest?.optString("trend_status", "Baseline Established") ?: "Baseline Established",
                    confidenceLabel = latest?.optString("confidence_label", "High") ?: "High",
                    oodFlag = latest?.optBoolean("ood_flag", false) ?: false,
                    checkScheduleLabel = p.optJSONObject("check_schedule")?.optString("status_label").orEmpty(),
                    checkOverdue = p.optJSONObject("check_schedule")?.optBoolean("overdue", false) ?: false,
                    bedsideExamSummary = latest?.optString("bedside_exam_summary").orEmpty()
                )
            )
        }

        tvKpiCritical.text = critical.toString()
        tvKpiHigh.text = highMod.toString()
        tvKpiTotal.text = patientsList.size.toString()
        tvStatPatients.text = patientsList.size.toString()

        applyWardFilter()

        val restoredIdx = if (prevId != null) patientsList.indexOfFirst { it.id == prevId } else -1
        selectedPatientIndex = if (restoredIdx >= 0) restoredIdx else 0
        refreshSelectedPatientLabel()
    }

    private fun setupWardTriageControls() {
        etWardSearch.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                wardSearchQuery = s?.toString()?.trim().orEmpty()
                btnClearWardSearch.visibility = if (wardSearchQuery.isNotEmpty()) View.VISIBLE else View.GONE
                applyWardFilter()
            }
        })
        btnClearWardSearch.setOnClickListener {
            etWardSearch.setText("")
        }
        chipFilterAll.setOnClickListener { setWardFilter("ALL") }
        chipFilterCritical.setOnClickListener { setWardFilter("CRITICAL") }
        chipFilterOverdue.setOnClickListener { setWardFilter("OVERDUE") }
        chipFilterHealthy.setOnClickListener { setWardFilter("HEALTHY") }
        updateWardFilterChipStyles()
    }

    private fun setWardFilter(mode: String) {
        wardFilterMode = if (wardFilterMode == mode && mode != "ALL") "ALL" else mode
        updateWardFilterChipStyles()
        applyWardFilter()
    }

    private fun updateWardFilterChipStyles() {
        val activeBg = ContextCompat.getColor(this, R.color.fg_primary)
        val activeFg = ContextCompat.getColor(this, R.color.white)
        val idleBg = ContextCompat.getColor(this, R.color.fg_surface)
        val idleFg = ContextCompat.getColor(this, R.color.fg_text_secondary)
        val pairs = listOf(
            chipFilterAll to "ALL",
            chipFilterCritical to "CRITICAL",
            chipFilterOverdue to "OVERDUE",
            chipFilterHealthy to "HEALTHY"
        )
        for ((tv, mode) in pairs) {
            val active = (wardFilterMode == mode)
            tv.backgroundTintList = ColorStateList.valueOf(if (active) activeBg else idleBg)
            tv.setTextColor(if (active) activeFg else idleFg)
        }
    }

    private fun applyWardFilter() {
        if (patientsList.isEmpty()) {
            cardEmptyWard.visibility = View.VISIBLE
            rvPatients.visibility = View.GONE
            tvWardFilterEmpty.visibility = View.GONE
            patientAdapter.submitList(emptyList())
            return
        }
        cardEmptyWard.visibility = View.GONE
        val q = wardSearchQuery.lowercase(Locale.US)
        val filtered = patientsList.filter { p ->
            val matchFilter = when (wardFilterMode) {
                "CRITICAL" -> p.riskTier == "Critical" || p.riskTier == "High"
                "OVERDUE" -> p.checkOverdue
                "HEALTHY" -> p.riskTier == "Low"
                else -> true
            }
            val matchQuery = q.isEmpty() ||
                p.fullName.lowercase(Locale.US).contains(q) ||
                p.mrn.lowercase(Locale.US).contains(q) ||
                p.flapType.lowercase(Locale.US).contains(q)
            matchFilter && matchQuery
        }
        tvWardFilterEmpty.visibility = if (filtered.isEmpty()) View.VISIBLE else View.GONE
        rvPatients.visibility = if (filtered.isEmpty()) View.GONE else View.VISIBLE
        patientAdapter.submitList(filtered)
    }

    private fun setupPhase2Controls() {
        tabAlertsActive.setOnClickListener {
            alertTabMode = "ACTIVE"
            renderFilteredAlerts()
        }
        tabAlertsHistory.setOnClickListener {
            alertTabMode = "HISTORY"
            renderFilteredAlerts()
        }
        renderFilteredAlerts()

        findViewById<LinearLayout>(R.id.row_theme_mode).setOnClickListener {
            showThemeModeDialog()
        }
        refreshThemeModeLabel()

        val examGroups = listOf(cgCapRefill, cgTurgor, cgPinprick, cgDopplerQual)
        examGroups.forEach { cg ->
            cg.setOnCheckedStateChangeListener { _, _ -> updateExamToggleBadge() }
        }
        updateExamToggleBadge()
    }

    private fun updateExamToggleBadge() {
        val count = listOf(cgCapRefill, cgTurgor, cgPinprick, cgDopplerQual)
            .count { it.checkedChipId != View.NO_ID }
        val expanded = layoutExamFields.visibility == View.VISIBLE
        val prefix = if (expanded) "−" else "+"
        tvToggleExamLabel.text = if (count > 0) {
            "$prefix Bedside Physical Exam ($count selected)"
        } else {
            "$prefix Bedside Physical Exam (Optional)"
        }
    }

    private fun resetVitalTileHints() {
        val muted = ContextCompat.getColor(this, R.color.fg_text_muted)
        listOf(tileIrTemp, tileCtrlTemp, tileSpo2, tileDoppler).forEach {
            it.setBackgroundResource(R.drawable.bg_vital_input)
        }
        tvHintIrTemp.text = "Ref 35.5–37.5°C"
        tvHintCtrlTemp.text = "ΔT normal < 1.5°C"
        tvHintSpo2.text = "Alert < 90%"
        tvHintDoppler.text = "Alert < 12 cm/s"
        listOf(tvHintIrTemp, tvHintCtrlTemp, tvHintSpo2, tvHintDoppler).forEach { it.setTextColor(muted) }
    }

    private fun setVitalTileError(tile: LinearLayout, hintView: TextView, errText: String) {
        tile.setBackgroundResource(R.drawable.bg_vital_input_error)
        hintView.text = errText
        hintView.setTextColor(ContextCompat.getColor(this, R.color.fg_danger))
    }

    private fun renderFilteredAlerts() {
        val activeList = currentAlerts.filter { !it.acknowledged }
        val reviewedRaw = currentAlerts.filter { it.acknowledged }

        val groupedHistory = mutableListOf<AlertItem>()
        val seenKeys = LinkedHashMap<String, MutableList<AlertItem>>()
        for (a in reviewedRaw) {
            val normMsg = a.message.replace(Regex("""^(URGENT|WARNING|WATCH|MONITOR)\s*\[[^\]]+\]:\s*""", RegexOption.IGNORE_CASE), "").trim()
            val key = "${a.patientName}|${a.riskTier}|$normMsg"
            seenKeys.getOrPut(key) { mutableListOf() }.add(a)
        }
        for ((_, group) in seenKeys) {
            val head = group.first()
            groupedHistory.add(head.copy(groupedCount = group.size))
        }

        tabAlertsActive.text = if (activeList.isNotEmpty()) "Needs Action (${activeList.size})" else "Needs Action"
        tabAlertsHistory.text = if (reviewedRaw.isNotEmpty()) "History (${reviewedRaw.size})" else "History"

        val activeBg = ContextCompat.getColor(this, R.color.fg_primary)
        val activeFg = ContextCompat.getColor(this, R.color.white)
        val idleBg = android.graphics.Color.TRANSPARENT
        val idleFg = ContextCompat.getColor(this, R.color.fg_text_secondary)

        val showingActive = (alertTabMode == "ACTIVE")
        tabAlertsActive.backgroundTintList = ColorStateList.valueOf(if (showingActive) activeBg else idleBg)
        tabAlertsActive.setTextColor(if (showingActive) activeFg else idleFg)
        tabAlertsHistory.backgroundTintList = ColorStateList.valueOf(if (!showingActive) activeBg else idleBg)
        tabAlertsHistory.setTextColor(if (!showingActive) activeFg else idleFg)

        val displayList = if (showingActive) activeList else groupedHistory
        tvAlertsSummary.text = if (showingActive) {
            if (activeList.isEmpty()) "All clear — 0 alerts pending review"
            else "${activeList.size} pending clinical review"
        } else {
            if (reviewedRaw.isEmpty()) "No reviewed alerts yet"
            else "${reviewedRaw.size} reviewed (${groupedHistory.size} grouped)"
        }

        if (displayList.isEmpty()) {
            cardEmptyAlerts.visibility = View.VISIBLE
            rvAlerts.visibility = View.GONE
            tvEmptyAlertsText.text = if (showingActive) {
                "All clear — no active flap compromise alerts pending review."
            } else {
                "No reviewed alert history recorded yet."
            }
        } else {
            cardEmptyAlerts.visibility = View.GONE
            rvAlerts.visibility = View.VISIBLE
        }
        alertAdapter.submitList(displayList)
    }

    private fun refreshThemeModeLabel() {
        tvActiveThemeLabel.text = when (SecurePrefs.getThemeMode(this)) {
            "light" -> "Daylight (Clinical Light)"
            "dark" -> "ICU Night (Low-Glare Dark)"
            else -> "Follow System"
        }
    }

    private fun showThemeModeDialog() {
        val labels = arrayOf("Follow System", "Daylight (Clinical Light)", "ICU Night (Low-Glare Dark)")
        val values = arrayOf("system", "light", "dark")
        val current = SecurePrefs.getThemeMode(this)
        val idx = values.indexOf(current).coerceAtLeast(0)
        MaterialAlertDialogBuilder(this)
            .setTitle("Display Appearance")
            .setSingleChoiceItems(labels, idx) { dialog, which ->
                SecurePrefs.setThemeMode(this, values[which])
                refreshThemeModeLabel()
                dialog.dismiss()
            }
            .show()
    }

    private fun selectedPatient(): PatientItem? {
        return if (selectedPatientIndex in patientsList.indices) patientsList[selectedPatientIndex] else null
    }

    private fun refreshSelectedPatientLabel() {
        val p = selectedPatient()
        if (p != null) {
            tvSelectedAvatar.text = initialsOf(p.fullName)
            tvSelectedPatient.text = p.fullName
            tvSelectedPatientSub.text = String.format(
                Locale.US, "%s  •  %s  •  %.1fh post-op", p.mrn, p.flapType, p.hoursPostOp
            )
            etSpo2.hint = p.spo2Pct?.let { String.format(Locale.US, "Last %.1f", it) } ?: "96.5"
            etDoppler.hint = p.dopplerCmS?.let { String.format(Locale.US, "Last %.1f", it) } ?: "20.5"
        } else {
            tvSelectedAvatar.text = "--"
            tvSelectedPatient.text = "No patients yet"
            tvSelectedPatientSub.text = "Enroll a patient in Ward to begin assessments"
        }
    }

    private fun showPatientPickerDialog() {
        if (patientsList.isEmpty()) {
            showNotice("Enroll a patient in the Ward tab first.")
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
        suppressNextAppLock = true
        val isSecure = (window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE) != 0
        val intent = Intent(this, PatientChartActivity::class.java).apply {
            putExtra("patient_id", patient.id)
            putExtra("mrn", patient.mrn)
            putExtra("full_name", patient.fullName)
            putExtra("age", patient.age)
            putExtra("flap_type", patient.flapType)
            putExtra("surgery_site", patient.surgerySite)
            putExtra("hours_post_op", patient.hoursPostOp)
            putExtra("risk_tier", patient.riskTier)
            putExtra("predicted_class", patient.predictedClass)
            putExtra("risk_score_pct", patient.riskScorePct)
            putExtra("has_delta", patient.tempDeltaC != null)
            putExtra("temp_delta_c", patient.tempDeltaC ?: 0.0)
            putExtra("has_spo2", patient.spo2Pct != null)
            putExtra("spo2_pct", patient.spo2Pct ?: 0.0)
            putExtra("has_doppler", patient.dopplerCmS != null)
            putExtra("doppler_cm_s", patient.dopplerCmS ?: 0.0)
            putExtra("has_visual", patient.visualScore != null)
            putExtra("visual_score", patient.visualScore ?: 0.0)
            putExtra("alert_message", patient.alertMessage)
            putExtra("trend_status", patient.trendStatus)
            putExtra("confidence_label", patient.confidenceLabel)
            putExtra("ood_flag", patient.oodFlag)
            putExtra("check_schedule_label", patient.checkScheduleLabel)
            putExtra("check_overdue", patient.checkOverdue)
            putExtra("bedside_exam_summary", patient.bedsideExamSummary)
            putExtra("server_url", serverUrl())
            putExtra("auth_token", authToken)
            putExtra("flag_secure", isSecure)
        }
        patientChartLauncher.launch(intent)
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
                tilMrn.error = "Required"
                valid = false
            }
            if (name.length < 2) {
                tilName.error = "Enter full name"
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
                tilSite.error = "Required"
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
                        showNotice("Enrolled $name.")
                        fetchPatients()
                    } else {
                        tilMrn.error = res.errorMessage
                    }
                }
            }
        }
        sheet.show()
    }

    private fun showSimulatorSheet() {
        if (selectedPatient() == null) {
            showNotice("Enroll a patient in the Ward tab first.")
            return
        }
        val sheet = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.sheet_simulator, null)
        sheet.setContentView(view)
        sheet.behavior.state = BottomSheetBehavior.STATE_EXPANDED
        sheet.behavior.skipCollapsed = true

        view.findViewById<MaterialButton>(R.id.btn_sim_venous).setOnClickListener {
            sheet.dismiss()
            triggerSimulation("venous_deteriorating", 7)
        }
        view.findViewById<MaterialButton>(R.id.btn_sim_arterial).setOnClickListener {
            sheet.dismiss()
            triggerSimulation("arterial_deteriorating", 7)
        }
        sheet.show()
    }

    private fun showWardDataSheet() {
        val sheet = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.sheet_ward_data, null)
        sheet.setContentView(view)
        sheet.behavior.state = BottomSheetBehavior.STATE_EXPANDED
        sheet.behavior.skipCollapsed = true

        view.findViewById<MaterialButton>(R.id.btn_load_samples).setOnClickListener {
            sheet.dismiss()
            loadSampleCases()
        }
        view.findViewById<MaterialButton>(R.id.btn_clear_ward).setOnClickListener {
            sheet.dismiss()
            confirmClearWard()
        }
        sheet.show()
    }

    private fun confirmDeletePatient(patient: PatientItem) {
        MaterialAlertDialogBuilder(this)
            .setTitle("Remove Patient?")
            .setMessage("Remove ${patient.fullName} (${patient.mrn}) and all associated readings?")
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
            suppressNextAppLock = true; flapCameraLauncher.launch(uri)
        } catch (e: Exception) {
            showNotice("Camera unavailable: ${e.message}")
        }
    }

    private fun analyzeFlapImageUri(uri: Uri) {
        tvVisionResult.visibility = View.VISIBLE
        tvVisionResult.text = "Analyzing flap color..."
        beginSync()
        executor.execute {
            try {
                val scaled = ImageUtils.decodeScaledRotatedBitmap(this, uri, 640)
                if (scaled == null) {
                    endSync()
                    mainHandler.post { tvVisionResult.text = "Could not load selected image." }
                    return@execute
                }
                val encoded = ImageUtils.toBase64Jpeg(scaled, 88)
                mainHandler.post {
                    ivFlapPreview.setImageBitmap(scaled)
                    layoutPreviewFrame.visibility = View.VISIBLE
                }

                val body = JSONObject().put("image_base64", encoded)
                controlSkinBase64?.let { body.put("control_image_base64", it) }
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
                            "\nNote: ${warnings.optString(0)}"
                        } else {
                            ""
                        }

                        tvVisionResult.text = String.format(
                            Locale.US,
                            "%s  •  Score %.0f/100 (%s)" + (if (res.json.optBoolean("calibrated_with_control", false)) " · Skin-Calibrated" else "") + "%s",
                            triage, score, qStatus, warnText
                        )
                    } else {
                        tvVisionResult.text = "Scan failed: ${res.errorMessage}"
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
            showNotice("Enroll a patient in the Ward tab first.")
            return
        }

        resetVitalTileHints()

        val irTemp = etIrTemp.text?.toString()?.trim()?.toDoubleOrNull()
        val ctrlTemp = etCtrlTemp.text?.toString()?.trim()?.toDoubleOrNull()
        val spo2 = etSpo2.text?.toString()?.trim()?.toDoubleOrNull()
        val doppler = etDoppler.text?.toString()?.trim()?.toDoubleOrNull()
        val hours = etHours.text?.toString()?.trim()?.toDoubleOrNull() ?: patient.hoursPostOp

        val providedCount = listOf(irTemp, ctrlTemp, spo2, doppler).count { it != null }
        if (providedCount < 2) {
            if (irTemp == null) setVitalTileError(tileIrTemp, tvHintIrTemp, "Enter reading")
            if (spo2 == null) setVitalTileError(tileSpo2, tvHintSpo2, "Enter reading")
            showNotice("Enter at least 2 bedside readings to assess flap risk.")
            return
        }

        var rangeOk = true
        if (irTemp != null && (irTemp < 20.0 || irTemp > 43.0)) { setVitalTileError(tileIrTemp, tvHintIrTemp, "Range 20–43°C"); rangeOk = false }
        if (ctrlTemp != null && (ctrlTemp < 30.0 || ctrlTemp > 43.0)) { setVitalTileError(tileCtrlTemp, tvHintCtrlTemp, "Range 30–43°C"); rangeOk = false }
        if (spo2 != null && (spo2 < 0.0 || spo2 > 100.0)) { setVitalTileError(tileSpo2, tvHintSpo2, "Range 0–100%"); rangeOk = false }
        if (doppler != null && (doppler < 0.0 || doppler > 60.0)) { setVitalTileError(tileDoppler, tvHintDoppler, "Range 0–60 cm/s"); rangeOk = false }
        if (!rangeOk) return

        beginSync()
        executor.execute {
            val payload = JSONObject().apply {
                if (irTemp != null) put("ir_flap_temp_c", irTemp)
                if (ctrlTemp != null) put("control_skin_temp_c", ctrlTemp)
                if (spo2 != null) put("spo2_pct", spo2)
                if (doppler != null) put("doppler_flow_cm_s", doppler)
                put("hours_post_op", hours)
                put("visual_redness_index", scannedRedness)
                put("visual_cyanosis_index", scannedCyanosis)
                put("visual_pallor_index", scannedPallor)
                selectedChipText(cgCapRefill)?.let { put("capillary_refill", it) }
                selectedChipText(cgTurgor)?.let { put("flap_turgor", it) }
                selectedChipText(cgPinprick)?.let { put("pinprick_bleeding", it) }
                selectedChipText(cgDopplerQual)?.let { put("doppler_quality", it) }
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
                } else if (res.statusCode == 0) {
                    offlineQueueDb.enqueue(patient.id, patient.fullName, payload)
                    updateOfflineQueueBadge()
                    showNotice("Offline — reading saved locally and will auto-sync.")
                } else {
                    showNotice("Assessment failed: ${res.errorMessage}")
                }
            }
        }
    }

    private fun triggerSimulation(scenario: String, step: Int) {
        val patient = selectedPatient() ?: return
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
        val (bgRes, titleRes) = when (tier) {
            "Critical" -> R.color.fg_danger_bg to R.color.fg_danger
            "High" -> R.color.fg_warning_bg to R.color.fg_warning
            "Moderate" -> R.color.fg_info_bg to R.color.fg_info
            else -> R.color.fg_success_bg to R.color.fg_success
        }
        cardPredictionResult.setCardBackgroundColor(ContextCompat.getColor(this, bgRes))
        tvPredictionTitle.setTextColor(ContextCompat.getColor(this, titleRes))
        val cleanMsg = msg
            .replace(Regex("""^(URGENT|WARNING|WATCH|MONITOR)\s*\[[^\]]+\]:\s*""", RegexOption.IGNORE_CASE), "")
            .trim()
        val trend = r.optString("trend_status", "")
        val conf = r.optString("confidence_label", "")
        val metaLine = buildList {
            if (trend.isNotBlank()) add("Trend: $trend")
            if (conf.isNotBlank()) add("Reliability: $conf")
        }.joinToString(" · ")
        tvPredictionTitle.text = "$predClass  •  $tier Risk (${String.format(Locale.US, "%.1f%%", riskPct)})"
        tvPredictionResult.text = if (metaLine.isNotBlank()) "$cleanMsg\n$metaLine" else cleanMsg
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
                unackCount = unack
                tvStatAlerts.text = unack.toString()

                // Native red badge on Alerts bottom nav icon
                val badge = bottomNav.getOrCreateBadge(R.id.nav_alerts)
                if (unack > 0) {
                    badge.isVisible = true
                    badge.number = unack
                    badge.backgroundColor = Color.parseColor("#E11D48")
                    badge.badgeTextColor = Color.WHITE
                } else {
                    badge.isVisible = false
                }

                // Soft alert pill ONLY on Ward tab
                if (unack > 0) {
                    cardWardAlertPill.visibility = View.VISIBLE
                    tvWardAlertText.text = if (unack == 1) {
                        "1 active flap alert needs review"
                    } else {
                        "$unack active flap alerts need review"
                    }
                } else {
                    cardWardAlertPill.visibility = View.GONE
                }
                updateAckAllVisibility()

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

                    val predClass = a.optString("predicted_class", "")
                    val riskPct = a.optDouble("risk_score_pct", -1.0)
                    if (!acked && notifiedAlertIds.add(id) && NotificationHelper.markAndCheckShouldNotify(this, id)) {
                        NotificationHelper.showSurgeonAlert(this, id, pName, tier, predClass, riskPct, msg)
                    }
                    val pId = a.optInt("patient_id", -1)
                    val createdIso = a.optString("created_at", "")
                    list.add(AlertItem(id, pName, tier, msg, acked, ackBy, pId, createdIso))
                }

                currentAlerts.clear()
                currentAlerts.addAll(list)
                renderFilteredAlerts()
            }
        }
    }

    private fun applyScreenPrivacyFlag() {
        if (SecurePrefs.isScreenPrivacyEnabled(this)) {
            window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }

    private fun isDeviceLockAvailable(): Boolean {
        val km = getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
        return km?.isDeviceSecure == true
    }

    private fun showAppLockOverlay() {
        isAppLocked = true
        val contentRoot = findViewById<ViewGroup>(android.R.id.content) ?: return
        if (appLockOverlay == null) {
            val dp = resources.displayMetrics.density
            val overlay = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER
                setBackgroundColor(Color.parseColor("#F4F7FA"))
                isClickable = true
                isFocusable = true
                elevation = 120f
                setPadding((32 * dp).toInt(), (32 * dp).toInt(), (32 * dp).toInt(), (32 * dp).toInt())
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT
                )
            }
            val icon = ImageView(this).apply {
                setImageResource(R.drawable.ic_brand_logo)
                layoutParams = LinearLayout.LayoutParams((64 * dp).toInt(), (64 * dp).toInt())
            }
            val title = TextView(this).apply {
                text = "FlapGuard AI Locked"
                textSize = 21f
                setTextColor(Color.parseColor("#0F172A"))
                gravity = Gravity.CENTER
                setPadding(0, (16 * dp).toInt(), 0, (6 * dp).toInt())
            }
            val sub = TextView(this).apply {
                text = "Verify with your fingerprint, face, or phone PIN to view patient data."
                textSize = 13f
                setTextColor(Color.parseColor("#64748B"))
                gravity = Gravity.CENTER
                setPadding(0, 0, 0, (24 * dp).toInt())
            }
            val btn = MaterialButton(this).apply {
                text = "Unlock FlapGuard"
                isAllCaps = false
                cornerRadius = (14 * dp).toInt()
                backgroundTintList = ColorStateList.valueOf(Color.parseColor("#0F4C81"))
                setTextColor(Color.WHITE)
                setOnClickListener { promptDeviceUnlock() }
            }
            overlay.addView(icon)
            overlay.addView(title)
            overlay.addView(sub)
            overlay.addView(btn)
            appLockOverlay = overlay
            contentRoot.addView(overlay)
        }
        appLockOverlay?.visibility = View.VISIBLE
        appLockOverlay?.bringToFront()
    }

    private fun hideAppLockOverlay() {
        isAppLocked = false
        appLockOverlay?.visibility = View.GONE
    }

    private fun onAppUnlocked() {
        needsReauthOnReturn = false
        hideAppLockOverlay()
        if (pendingEnableAppLock) {
            pendingEnableAppLock = false
            SecurePrefs.setAppLockEnabled(this, true)
            showNotice("App lock enabled — phone unlock required whenever you reopen FlapGuard.")
        }
    }

    private fun onAppUnlockFailedOrCancelled(errMsg: String?) {
        if (pendingEnableAppLock) {
            pendingEnableAppLock = false
            ignoreAppLockSwitchChange = true
            switchAppLock.isChecked = false
            ignoreAppLockSwitchChange = false
            SecurePrefs.setAppLockEnabled(this, false)
            hideAppLockOverlay()
            showNotice(errMsg ?: "App lock not enabled — verification cancelled.")
        } else if (SecurePrefs.isAppLockEnabled(this)) {
            showAppLockOverlay()
        }
    }

    @Suppress("DEPRECATION")
    private fun launchKeyguardFallback() {
        val km = getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
        val intent = km?.createConfirmDeviceCredentialIntent(
            "Unlock FlapGuard AI",
            "Verify your identity to access patient data"
        )
        if (intent != null) {
            suppressNextAppLock = true
            deviceCredentialLauncher.launch(intent)
        } else {
            onAppUnlockFailedOrCancelled("Phone lock screen is unavailable.")
        }
    }

    private fun promptDeviceUnlock() {
        if (!isDeviceLockAvailable()) {
            onAppUnlockFailedOrCancelled("Set up a phone PIN or fingerprint in Android Settings first.")
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            try {
                val prompt = BiometricPrompt.Builder(this)
                    .setTitle("Unlock FlapGuard AI")
                    .setSubtitle("Verify your identity to access patient data")
                    .setAllowedAuthenticators(
                        BiometricManager.Authenticators.BIOMETRIC_STRONG or
                            BiometricManager.Authenticators.BIOMETRIC_WEAK or
                            BiometricManager.Authenticators.DEVICE_CREDENTIAL
                    )
                    .build()
                prompt.authenticate(
                    CancellationSignal(),
                    mainExecutor,
                    object : BiometricPrompt.AuthenticationCallback() {
                        override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult?) {
                            mainHandler.post { onAppUnlocked() }
                        }

                        override fun onAuthenticationError(errorCode: Int, errString: CharSequence?) {
                            mainHandler.post {
                                if (errorCode == BiometricPrompt.BIOMETRIC_ERROR_NO_BIOMETRICS ||
                                    errorCode == BiometricPrompt.BIOMETRIC_ERROR_HW_UNAVAILABLE ||
                                    errorCode == BiometricPrompt.BIOMETRIC_ERROR_HW_NOT_PRESENT
                                ) {
                                    launchKeyguardFallback()
                                } else {
                                    onAppUnlockFailedOrCancelled(errString?.toString())
                                }
                            }
                        }
                    }
                )
            } catch (_: Exception) {
                launchKeyguardFallback()
            }
        } else {
            launchKeyguardFallback()
        }
    }

    private fun confirmSignOutOtherDevices() {
        MaterialAlertDialogBuilder(this)
            .setTitle("Sign Out Other Devices?")
            .setMessage("This immediately revokes all active FlapGuard sessions on any other phone signed into your account.")
            .setPositiveButton("Revoke Others") { _, _ ->
                beginSync()
                executor.execute {
                    val res = ApiClient.request(
                        serverUrl(),
                        "/mobile/sessions/revoke-all",
                        "POST",
                        JSONObject().put("include_current", false),
                        authToken
                    )
                    endSync()
                    mainHandler.post {
                        if (res.ok) {
                            val rotated = res.json.optString("token", "")
                            if (rotated.isNotBlank()) {
                                authToken = rotated
                                SecurePrefs.saveAuthToken(this, rotated)
                            }
                            showNotice("All other mobile sessions have been signed out.")
                        } else {
                            showNotice("Failed: ${res.errorMessage}")
                        }
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showDeleteAccountSheet() {
        val sheet = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.sheet_delete_account, null)
        sheet.setContentView(view)
        sheet.behavior.state = BottomSheetBehavior.STATE_EXPANDED
        sheet.behavior.skipCollapsed = true

        val deadlineStr = DateTimeFormatter.ofPattern("dd MMM yyyy, HH:mm", Locale.US)
            .format(LocalDateTime.now().plusDays(30))
        view.findViewById<TextView>(R.id.tv_delete_deadline_preview).text =
            "Your personal profile will be permanently deleted on $deadlineStr (30 days from now). " +
            "You can sign back in anytime before that exact time to cancel and restore your account. " +
            "Clinical ward records remain anonymized for audit safety."

        val tilPw = view.findViewById<TextInputLayout>(R.id.til_delete_password)
        val etPw = view.findViewById<TextInputEditText>(R.id.et_delete_password)
        val btnCancel = view.findViewById<MaterialButton>(R.id.btn_cancel_delete_sheet)
        val btnConfirm = view.findViewById<MaterialButton>(R.id.btn_confirm_delete_account)

        btnCancel.setOnClickListener { sheet.dismiss() }
        btnConfirm.setOnClickListener {
            tilPw.error = null
            val pw = etPw.text?.toString().orEmpty()
            if (pw.isBlank()) {
                tilPw.error = "Enter your password"
                return@setOnClickListener
            }
            btnConfirm.isEnabled = false
            executor.execute {
                val res = ApiClient.request(
                    serverUrl(),
                    "/mobile/account/delete",
                    "POST",
                    JSONObject().put("password", pw),
                    authToken
                )
                mainHandler.post {
                    btnConfirm.isEnabled = true
                    if (res.ok) {
                        sheet.dismiss()
                        AlertMonitorService.setEnabled(this, false)
                        AlertJobService.cancel(this)
                        NotificationHelper.clearNotifiedHistory(this)
                        SecurePrefs.clearAuthToken(this)
                        getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE).edit()
                            .remove("user_photo")
                            .apply()
                        startActivity(Intent(this, MainActivity::class.java))
                        finish()
                    } else {
                        tilPw.error = res.errorMessage
                    }
                }
            }
        }
        sheet.show()
    }

    private fun selectedChipText(group: ChipGroup): String? {
        val id = group.checkedChipId
        if (id == View.NO_ID) return null
        return group.findViewById<Chip>(id)?.text?.toString()?.trim()?.takeIf { it.isNotBlank() }
    }

    private fun showSbarHandoffSheet() {
        suppressNextAppLock = true
        val isSecure = (window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE) != 0
        val intent = Intent(this, SbarHandoffActivity::class.java).apply {
            putExtra("server_url", serverUrl())
            putExtra("auth_token", authToken)
            putExtra("flag_secure", isSecure)
        }
        sbarHandoffLauncher.launch(intent)
    }

    private fun openPatientFromAlert(alert: AlertItem) {
        val match = patientsList.firstOrNull { it.id == alert.patientId }
            ?: patientsList.firstOrNull { it.fullName.equals(alert.patientName, ignoreCase = true) }
        if (match != null) {
            showPatientDetailSheet(match)
        } else {
            showNotice("${alert.patientName} is no longer in the active ward.")
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
                    (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(readingId)
                    showNotice("Alert marked reviewed.")
                    lastAlertsSignature = ""
                    fetchAlerts(silent = false)
                } else {
                    showNotice("Failed: ${res.errorMessage}")
                }
            }
        }
    }

    private fun acknowledgeAllAlerts() {
        val pendingIds = currentAlerts.filter { !it.acknowledged }.map { it.id }
        if (pendingIds.isEmpty()) return
        beginSync()
        executor.execute {
            for (id in pendingIds) {
                val ackRes = ApiClient.request(serverUrl(), "/mobile/alerts/$id/acknowledge", "POST", JSONObject(), authToken)
                if (ackRes.ok) {
                    (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(id)
                }
            }
            endSync()
            mainHandler.post {
                showNotice("Marked ${pendingIds.size} alert(s) reviewed.")
                lastAlertsSignature = ""
                fetchAlerts(silent = false)
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
                    0 -> { suppressNextAppLock = true; profileGalleryLauncher.launch("image/*") }
                    1 -> {
                        try {
                            val uri = createTempCameraUri("avatar")
                            pendingProfileCameraUri = uri
                            suppressNextAppLock = true; profileCameraLauncher.launch(uri)
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
                        showNotice("Upload failed: ${res.errorMessage}")
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
            showNotice("Enroll a patient in the Ward tab first.")
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
        if (selectedPatientIndex in patientsList.indices) {
            spPatient.setSelection(selectedPatientIndex)
        }

        val optNormal = view.findViewById<LinearLayout>(R.id.chip_outcome_normal)
        val optVenous = view.findViewById<LinearLayout>(R.id.chip_outcome_venous)
        val optArterial = view.findViewById<LinearLayout>(R.id.chip_outcome_arterial)
        val options = listOf(
            optNormal to "Normal",
            optVenous to "Venous Compromise",
            optArterial to "Arterial Compromise"
        )
        var chosenOutcome = "Normal"

        fun updateOutcomeSelection() {
            val activeBg = ContextCompat.getColor(this, R.color.fg_primary_soft)
            val idleBg = ContextCompat.getColor(this, R.color.fg_input_bg)
            for ((cardOpt, value) in options) {
                cardOpt.backgroundTintList = ColorStateList.valueOf(
                    if (value == chosenOutcome) activeBg else idleBg
                )
            }
        }
        for ((cardOpt, value) in options) {
            cardOpt.setOnClickListener {
                chosenOutcome = value
                updateOutcomeSelection()
            }
        }
        updateOutcomeSelection()

        val etNotes = view.findViewById<EditText>(R.id.et_outcome_notes_sheet)
        val btnSave = view.findViewById<MaterialButton>(R.id.btn_save_outcome_sheet)

        btnSave.setOnClickListener {
            val patient = patientsList[spPatient.selectedItemPosition]
            val notes = etNotes.text?.toString()?.trim().orEmpty()

            btnSave.isEnabled = false
            beginSync()
            executor.execute {
                val body = JSONObject().apply {
                    put("patient_id", patient.id)
                    put("verified_outcome", chosenOutcome)
                    put("surgeon_notes", notes)
                }
                val res = ApiClient.request(serverUrl(), "/mobile/outcomes", "POST", body, authToken)
                endSync()
                mainHandler.post {
                    btnSave.isEnabled = true
                    if (res.ok) {
                        sheet.dismiss()
                        showNotice("Confirmed outcome saved for continuous learning.")
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
            if (curr.isBlank()) { tilCurr.error = "Required"; return@setOnClickListener }
            if (next.length < 8) { tilNew.error = "8+ characters"; return@setOnClickListener }

            btnUpdate.isEnabled = false
            beginSync()
            executor.execute {
                val body = JSONObject().put("current_password", curr).put("new_password", next)
                val res = ApiClient.request(serverUrl(), "/mobile/profile/password", "POST", body, authToken)
                endSync()
                mainHandler.post {
                    btnUpdate.isEnabled = true
                    if (res.ok) {
                        val rotated = res.json.optString("token", "")
                        if (rotated.isNotBlank()) {
                            authToken = rotated
                            SecurePrefs.saveAuthToken(this, rotated)
                        }
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
        val activeColor = Color.parseColor("#0B3B66")
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
                    showNotice(if (created > 0) "Loaded $created sample cases." else "Sample cases already loaded.")
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
            .setMessage("This removes all patients, readings, and alerts from the active database.")
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

    private fun buildInitialsBadgeBitmap(name: String, isCritical: Boolean): Bitmap {
        val size = 128
        val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bmp)
        val cx = size / 2f
        val cy = size / 2f

        val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor(if (isCritical) "#FFF1F2" else "#FFFBEB")
            style = Paint.Style.FILL
        }
        canvas.drawCircle(cx, cy, cx - 2f, bgPaint)

        val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor(if (isCritical) "#FECDD3" else "#FDE68A")
            style = Paint.Style.STROKE
            strokeWidth = 4f
        }
        canvas.drawCircle(cx, cy, cx - 4f, ringPaint)

        val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.parseColor(if (isCritical) "#E11D48" else "#D97706")
            textSize = 44f
            typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
            textAlign = Paint.Align.CENTER
        }
        val textY = cy - (textPaint.descent() + textPaint.ascent()) / 2f
        canvas.drawText(initialsOf(name), cx, textY, textPaint)
        return bmp
    }

    private fun pushSurgeonNotification(
        id: Int,
        patientName: String,
        riskTier: String,
        predictedClass: String,
        riskScorePct: Double,
        rawMessage: String
    ) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }

        val isCritical = riskTier.equals("Critical", ignoreCase = true)
        val accentColor = Color.parseColor(if (isCritical) "#E11D48" else "#D97706")

        val cleanTitle = if (predictedClass.isNotBlank()) {
            "$patientName  ·  $predictedClass"
        } else {
            "$patientName  ·  $riskTier Flap Alert"
        }

        val cleanedRaw = rawMessage
            .replace(Regex("^URGENT\\s*\\[[^]]+]:\\s*"), "")
            .trim()
        val adviceLine = if (cleanedRaw.contains("Immediate", ignoreCase = true)) {
            "Immediate bedside flap inspection advised."
        } else {
            cleanedRaw.ifBlank { "Tap to inspect bedside vitals and review." }
        }

        val riskPrefix = if (riskScorePct >= 0.0) {
            String.format(Locale.US, "%.0f%% risk", riskScorePct)
        } else {
            "$riskTier risk"
        }

        val collapsedText = "$riskPrefix — $adviceLine"
        val expandedText = "$riskPrefix ($riskTier)\n$adviceLine"

        // Tap notification -> open FlapGuard AI straight to the Alerts tab
        val openIntent = Intent(this, DashboardActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra("open_tab", "alerts")
            putExtra(AlertActionReceiver.EXTRA_ALERT_ID, id)
        }
        val openPendingIntent = PendingIntent.getActivity(
            this,
            id * 10 + 1,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        // Inline "Mark Reviewed" action directly from notification shade / lock screen
        val reviewIntent = Intent(this, AlertActionReceiver::class.java).apply {
            action = AlertActionReceiver.ACTION_MARK_REVIEWED
            putExtra(AlertActionReceiver.EXTRA_ALERT_ID, id)
        }
        val reviewPendingIntent = PendingIntent.getBroadcast(
            this,
            id * 10 + 2,
            reviewIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(this, "flap_alerts_channel")
            .setSmallIcon(R.drawable.ic_stat_flapguard)
            .setLargeIcon(buildInitialsBadgeBitmap(patientName, isCritical))
            .setColor(accentColor)
            .setSubText(if (isCritical) "Critical Alert" else "$riskTier Priority")
            .setContentTitle(cleanTitle)
            .setContentText(collapsedText)
            .setStyle(
                NotificationCompat.BigTextStyle()
                    .setBigContentTitle(cleanTitle)
                    .bigText(expandedText)
            )
            .setContentIntent(openPendingIntent)
            .addAction(0, "Inspect", openPendingIntent)
            .addAction(0, "Mark Reviewed", reviewPendingIntent)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setGroup("flapguard_urgent_alerts")
            .setAutoCancel(true)
            .build()

        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(id, notification)
    }
}
