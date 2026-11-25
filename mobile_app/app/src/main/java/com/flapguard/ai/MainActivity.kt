package com.flapguard.ai

import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.ArrayAdapter
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import org.json.JSONObject
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private val executor = Executors.newFixedThreadPool(2)
    private val mainHandler = Handler(Looper.getMainLooper())
    private var isRegisterMode = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val prefs = getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
        if (!prefs.getString("auth_token", null).isNullOrBlank()) {
            startActivity(Intent(this, DashboardActivity::class.java))
            finish()
            return
        }

        setContentView(R.layout.activity_main)

        val tvConnectionPill = findViewById<TextView>(R.id.tv_connection_pill)
        val btnConnectionSettings = findViewById<ImageButton>(R.id.btn_connection_settings)
        val btnModeLogin = findViewById<MaterialButton>(R.id.btn_mode_login)
        val btnModeRegister = findViewById<MaterialButton>(R.id.btn_mode_register)
        val tilFullName = findViewById<TextInputLayout>(R.id.til_full_name)
        val etFullName = findViewById<TextInputEditText>(R.id.et_full_name)
        val layoutRole = findViewById<LinearLayout>(R.id.layout_role_container)
        val spinnerRole = findViewById<Spinner>(R.id.spinner_role)
        val etEmail = findViewById<TextInputEditText>(R.id.et_email)
        val etPassword = findViewById<TextInputEditText>(R.id.et_password)
        val btnSubmit = findViewById<MaterialButton>(R.id.btn_auth_submit)
        val progressAuth = findViewById<ProgressBar>(R.id.progress_auth)
        val tvStatus = findViewById<TextView>(R.id.tv_auth_status)

        fun refreshConnectionBadge() {
            val usb = ApiClient.isLocalUsbMode(this)
            tvConnectionPill.text = if (usb) "Offline USB Mode" else "Live Cloud"
            val bg = if (usb) "#FEF3C7" else "#E8F1FA"
            val fg = if (usb) "#B45309" else "#0F4C81"
            tvConnectionPill.backgroundTintList = ColorStateList.valueOf(Color.parseColor(bg))
            tvConnectionPill.setTextColor(Color.parseColor(fg))
        }
        refreshConnectionBadge()

        btnConnectionSettings.setOnClickListener {
            val options = arrayOf(
                "Live Cloud (flap-guard-ai.onrender.com)",
                "Offline USB Viva Mode (127.0.0.1:5000)"
            )
            val checked = if (ApiClient.isLocalUsbMode(this)) 1 else 0
            MaterialAlertDialogBuilder(this)
                .setTitle("Server Connection")
                .setSingleChoiceItems(options, checked) { dialog, which ->
                    ApiClient.setLocalUsbMode(this, which == 1)
                    refreshConnectionBadge()
                    tvStatus.text = ""
                    dialog.dismiss()
                }
                .setNegativeButton("Close", null)
                .show()
        }

        val roles = listOf("Surgeon", "Nurse", "Resident", "Admin")
        spinnerRole.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, roles)

        fun updateMode(register: Boolean) {
            isRegisterMode = register
            tilFullName.visibility = if (register) View.VISIBLE else View.GONE
            layoutRole.visibility = if (register) View.VISIBLE else View.GONE
            btnSubmit.text = if (register) "Create Clinician Account" else "Sign In"

            val activeBg = ColorStateList.valueOf(Color.parseColor("#0F4C81"))
            val idleBg = ColorStateList.valueOf(Color.parseColor("#F1F5F9"))
            btnModeLogin.backgroundTintList = if (!register) activeBg else idleBg
            btnModeLogin.setTextColor(Color.parseColor(if (!register) "#FFFFFF" else "#475569"))
            btnModeRegister.backgroundTintList = if (register) activeBg else idleBg
            btnModeRegister.setTextColor(Color.parseColor(if (register) "#FFFFFF" else "#475569"))
            tvStatus.text = ""
        }

        btnModeLogin.setOnClickListener { updateMode(false) }
        btnModeRegister.setOnClickListener { updateMode(true) }

        btnSubmit.setOnClickListener {
            val serverUrl = ApiClient.getServerUrl(this)
            val email = etEmail.text?.toString()?.trim().orEmpty()
            val password = etPassword.text?.toString().orEmpty()
            val fullName = etFullName.text?.toString()?.trim().orEmpty()
            val role = spinnerRole.selectedItem.toString()

            if (email.isBlank() || !email.contains("@") || password.length < 8) {
                tvStatus.setTextColor(Color.parseColor("#DC2626"))
                tvStatus.text = "Enter a valid email and password (8+ characters)."
                return@setOnClickListener
            }
            if (isRegisterMode && fullName.length < 2) {
                tvStatus.setTextColor(Color.parseColor("#DC2626"))
                tvStatus.text = "Enter your full name to create an account."
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
                        prefs.edit()
                            .putString("auth_token", token)
                            .putString("user_name", userObj?.optString("full_name") ?: email)
                            .putString("user_role", userObj?.optString("role") ?: "Surgeon")
                            .putString("user_email", userObj?.optString("email") ?: email)
                            .apply()

                        startActivity(Intent(this, DashboardActivity::class.java))
                        finish()
                    } else {
                        tvStatus.setTextColor(Color.parseColor("#DC2626"))
                        tvStatus.text = result.errorMessage
                    }
                }
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        executor.shutdownNow()
    }
}
