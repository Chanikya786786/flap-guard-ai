package com.flapguard.ai

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private var isRegisterMode = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val prefs = getSharedPreferences("flapguard_prefs", Context.MODE_PRIVATE)
        if (!prefs.getString("auth_token", null).isNullOrBlank()) {
            startActivity(Intent(this, DashboardActivity::class.java))
            finish()
            return
        }

        setContentView(R.layout.activity_main)

        val etServerUrl = findViewById<EditText>(R.id.et_server_url)
        val btnModeLogin = findViewById<Button>(R.id.btn_mode_login)
        val btnModeRegister = findViewById<Button>(R.id.btn_mode_register)
        val etFullName = findViewById<EditText>(R.id.et_full_name)
        val spinnerRole = findViewById<Spinner>(R.id.spinner_role)
        val etEmail = findViewById<EditText>(R.id.et_email)
        val etPassword = findViewById<EditText>(R.id.et_password)
        val btnSubmit = findViewById<Button>(R.id.btn_auth_submit)
        val btnDemoFill = findViewById<Button>(R.id.btn_demo_fill)
        val tvStatus = findViewById<TextView>(R.id.tv_auth_status)

        etServerUrl.setText(prefs.getString("server_url", "http://127.0.0.1:5000"))

        val roles = listOf("Surgeon", "Nurse", "Resident", "Admin")
        spinnerRole.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, roles)

        fun updateMode(register: Boolean) {
            isRegisterMode = register
            etFullName.visibility = if (register) View.VISIBLE else View.GONE
            spinnerRole.visibility = if (register) View.VISIBLE else View.GONE
            btnSubmit.text = if (register) "Create Clinician Account" else "Sign In"
            btnModeLogin.setBackgroundColor(Color.parseColor(if (!register) "#0F4C81" else "#64748B"))
            btnModeRegister.setBackgroundColor(Color.parseColor(if (register) "#0F4C81" else "#64748B"))
        }

        btnModeLogin.setOnClickListener { updateMode(false) }
        btnModeRegister.setOnClickListener { updateMode(true) }

        btnDemoFill.setOnClickListener {
            updateMode(false)
            etEmail.setText("surgeon@flapguard.local")
            etPassword.setText("Surgeon@123")
            tvStatus.setTextColor(Color.parseColor("#15803D"))
            tvStatus.text = "Demo surgeon credentials filled. Tap Sign In."
        }

        btnSubmit.setOnClickListener {
            val serverUrl = etServerUrl.text.toString().trim().ifBlank { "http://127.0.0.1:5000" }
            val email = etEmail.text.toString().trim()
            val password = etPassword.text.toString()
            val fullName = etFullName.text.toString().trim()
            val role = spinnerRole.selectedItem.toString()

            if (email.isBlank() || password.length < 8) {
                tvStatus.setTextColor(Color.parseColor("#DC2626"))
                tvStatus.text = "Enter a valid email and password (at least 8 characters)."
                return@setOnClickListener
            }
            if (isRegisterMode && fullName.length < 2) {
                tvStatus.setTextColor(Color.parseColor("#DC2626"))
                tvStatus.text = "Enter your full name to register."
                return@setOnClickListener
            }

            btnSubmit.isEnabled = false
            tvStatus.setTextColor(Color.parseColor("#0F4C81"))
            tvStatus.text = "Connecting to $serverUrl ..."

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
                    if (result.ok) {
                        val token = result.json.optString("token")
                        val userObj = result.json.optJSONObject("user")
                        prefs.edit()
                            .putString("server_url", serverUrl)
                            .putString("auth_token", token)
                            .putString("user_name", userObj?.optString("full_name") ?: email)
                            .putString("user_role", userObj?.optString("role") ?: "Surgeon")
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
}