package com.flapguard.ai

import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private val executor = Executors.newFixedThreadPool(2)
    private val mainHandler = Handler(Looper.getMainLooper())
    private var isRegisterMode = false

    override fun onCreate(savedInstanceState: Bundle?) {
        SecurePrefs.applySavedTheme(this)
        super.onCreate(savedInstanceState)

        val prefs = getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
        if (SecurePrefs.getAuthToken(this).isNotBlank()) {
            startActivity(Intent(this, DashboardActivity::class.java))
            finish()
            return
        }

        setContentView(R.layout.activity_main)

        val tvConnectionPill = findViewById<TextView>(R.id.tv_connection_pill)
        val btnConnectionSettings = findViewById<LinearLayout>(R.id.btn_connection_settings)
        val btnModeLogin = findViewById<MaterialButton>(R.id.btn_mode_login)
        val btnModeRegister = findViewById<MaterialButton>(R.id.btn_mode_register)
        val tilFullName = findViewById<TextInputLayout>(R.id.til_full_name)
        val etFullName = findViewById<TextInputEditText>(R.id.et_full_name)
        val layoutRole = findViewById<LinearLayout>(R.id.layout_role_container)
        val chipGroupRole = findViewById<ChipGroup>(R.id.chip_group_role)
        val tilEmail = findViewById<TextInputLayout>(R.id.til_email)
        val etEmail = findViewById<TextInputEditText>(R.id.et_email)
        val tilPassword = findViewById<TextInputLayout>(R.id.til_password)
        val etPassword = findViewById<TextInputEditText>(R.id.et_password)
        val btnSubmit = findViewById<MaterialButton>(R.id.btn_auth_submit)
        val progressAuth = findViewById<ProgressBar>(R.id.progress_auth)
        val tvStatus = findViewById<TextView>(R.id.tv_auth_status)

        fun refreshConnectionBadge() {
            val usb = ApiClient.isLocalUsbMode(this)
            tvConnectionPill.text = if (usb) {
                "Local USB Mode  •  Switch"
            } else {
                "Live Cloud  •  Switch"
            }
        }
        refreshConnectionBadge()

        btnConnectionSettings.setOnClickListener {
            showConnectionSheet {
                refreshConnectionBadge()
                tvStatus.text = ""
            }
        }

        fun updateMode(register: Boolean) {
            isRegisterMode = register
            tilFullName.visibility = if (register) View.VISIBLE else View.GONE
            layoutRole.visibility = if (register) View.VISIBLE else View.GONE
            btnSubmit.text = if (register) "Create Clinician Account" else "Sign In"

            val activeBg = ColorStateList.valueOf(Color.parseColor("#0F4C81"))
            val idleBg = ColorStateList.valueOf(Color.TRANSPARENT)
            btnModeLogin.backgroundTintList = if (!register) activeBg else idleBg
            btnModeLogin.setTextColor(Color.parseColor(if (!register) "#FFFFFF" else "#475569"))
            btnModeRegister.backgroundTintList = if (register) activeBg else idleBg
            btnModeRegister.setTextColor(Color.parseColor(if (register) "#FFFFFF" else "#475569"))

            tilFullName.error = null
            tilEmail.error = null
            tilPassword.error = null
            tvStatus.text = ""
        }

        btnModeLogin.setOnClickListener { updateMode(false) }
        btnModeRegister.setOnClickListener { updateMode(true) }

        btnSubmit.setOnClickListener {
            tilFullName.error = null
            tilEmail.error = null
            tilPassword.error = null

            val serverUrl = ApiClient.getServerUrl(this)
            val email = etEmail.text?.toString()?.trim().orEmpty()
            val password = etPassword.text?.toString().orEmpty()
            val fullName = etFullName.text?.toString()?.trim().orEmpty()
            val checkedRoleId = chipGroupRole.checkedChipId
            val role = findViewById<Chip>(checkedRoleId)?.text?.toString() ?: "Surgeon"

            if (isRegisterMode && fullName.length < 2) {
                tilFullName.error = "Enter your full name"
                return@setOnClickListener
            }
            if (email.isBlank() || !email.contains("@")) {
                tilEmail.error = "Enter a valid hospital email"
                return@setOnClickListener
            }
            if (password.length < 8) {
                tilPassword.error = "Password must be 8+ characters"
                tvStatus.setTextColor(Color.parseColor("#DC2626"))
                tvStatus.text = "Enter a valid email and password (8+ characters)."
                return@setOnClickListener
            }

            btnSubmit.isEnabled = false
            progressAuth.visibility = View.VISIBLE
            tvStatus.setTextColor(Color.parseColor("#0F4C81"))
            tvStatus.text = if (ApiClient.isLocalUsbMode(this)) {
                "Connecting to local USB server..."
            } else {
                "Signing in to FlapGuard Cloud..."
            }

            executor.execute {
                val payload = JSONObject().apply {
                    put("email", email)
                    put("password", password)
                    if (isRegisterMode) {
                        put("full_name", fullName)
                        put("role", role)
                    }
                }
                val endpoint = if (isRegisterMode) "/mobile/register" else "/mobile/login"
                val result = ApiClient.request(serverUrl, endpoint, "POST", payload)

                mainHandler.post {
                    btnSubmit.isEnabled = true
                    progressAuth.visibility = View.GONE
                    if (result.ok) {
                        val token = result.json.optString("token")
                        val userObj = result.json.optJSONObject("user")
                        val photo = userObj?.optString("profile_photo", "").orEmpty()
                        SecurePrefs.saveAuthToken(this, token)
                        prefs.edit()
                            .putString("user_name", userObj?.optString("full_name") ?: email)
                            .putString("user_role", userObj?.optString("role") ?: "Surgeon")
                            .putString("user_email", userObj?.optString("email") ?: email)
                            .putString("user_photo", if (photo == "null") "" else photo)
                            .apply()

                        startActivity(Intent(this, DashboardActivity::class.java))
                        finish()
                    } else if (!isRegisterMode && result.json.optString("code") == "ACCOUNT_DELETION_PENDING") {
                        val deadlineIso = result.json.optString("scheduled_deletion_at", "")
                        val daysRem = result.json.optInt("days_remaining", 30)
                        val hrsRem = result.json.optInt("hours_remaining", 0)
                        val formattedDeadline = try {
                            val inst = Instant.parse(deadlineIso).atZone(ZoneId.systemDefault())
                            DateTimeFormatter.ofPattern("dd MMM yyyy, HH:mm", Locale.US).format(inst)
                        } catch (_: Exception) {
                            deadlineIso
                        }
                        tvStatus.setTextColor(Color.parseColor("#D97706"))
                        tvStatus.text = "Deletion scheduled ($daysRem d $hrsRem h left)."
                        MaterialAlertDialogBuilder(this)
                            .setTitle("Restore Your Account?")
                            .setMessage(
                                "This account is scheduled for permanent deletion on $formattedDeadline " +
                                "($daysRem days, $hrsRem hours remaining).\n\nCancel deletion and sign back in?"
                            )
                            .setPositiveButton("Restore Account") { _, _ ->
                                btnSubmit.isEnabled = false
                                progressAuth.visibility = View.VISIBLE
                                executor.execute {
                                    val restoreBody = JSONObject()
                                        .put("email", email)
                                        .put("password", password)
                                        .put("restore", true)
                                    val rRes = ApiClient.request(serverUrl, "/mobile/login", "POST", restoreBody)
                                    mainHandler.post {
                                        btnSubmit.isEnabled = true
                                        progressAuth.visibility = View.GONE
                                        if (rRes.ok) {
                                            val tok = rRes.json.optString("token")
                                            val uObj = rRes.json.optJSONObject("user")
                                            val ph = uObj?.optString("profile_photo", "").orEmpty()
                                            SecurePrefs.saveAuthToken(this, tok)
                                            prefs.edit()
                                                .putString("user_name", uObj?.optString("full_name") ?: email)
                                                .putString("user_role", uObj?.optString("role") ?: "Surgeon")
                                                .putString("user_email", uObj?.optString("email") ?: email)
                                                .putString("user_photo", if (ph == "null") "" else ph)
                                                .apply()
                                            startActivity(Intent(this, DashboardActivity::class.java))
                                            finish()
                                        } else {
                                            tvStatus.setTextColor(Color.parseColor("#DC2626"))
                                            tvStatus.text = rRes.errorMessage
                                        }
                                    }
                                }
                            }
                            .setNegativeButton("Keep Scheduled", null)
                            .show()
                    } else {
                        tvStatus.setTextColor(Color.parseColor("#DC2626"))
                        tvStatus.text = result.errorMessage
                    }
                }
            }
        }
    }

    private fun showConnectionSheet(onChanged: () -> Unit) {
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
            onChanged()
            sheet.dismiss()
        }
        usbCard.setOnClickListener {
            ApiClient.setLocalUsbMode(this, true)
            onChanged()
            sheet.dismiss()
        }
        sheet.show()
    }

    override fun onDestroy() {
        super.onDestroy()
        executor.shutdownNow()
    }
}
