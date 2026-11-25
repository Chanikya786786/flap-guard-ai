package com.flapguard.ai

import android.content.Context
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

data class ApiResult(
    val ok: Boolean,
    val statusCode: Int,
    val json: JSONObject,
    val errorMessage: String = ""
)

object ApiClient {

    const val PREFS_NAME = "flapguard_prefs_v2"
    const val CLOUD_URL = "https://flap-guard-ai.onrender.com"
    const val LOCAL_USB_URL = "http://127.0.0.1:5000"

    fun isLocalUsbMode(context: Context): Boolean {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.getBoolean("use_local_usb", false)
    }

    fun setLocalUsbMode(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean("use_local_usb", enabled)
            .apply()
    }

    fun getServerUrl(context: Context): String {
        return if (isLocalUsbMode(context)) LOCAL_USB_URL else CLOUD_URL
    }

    fun request(
        baseUrl: String,
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        token: String? = null
    ): ApiResult {
        var conn: HttpURLConnection? = null
        return try {
            val cleanBase = baseUrl.trim().trimEnd('/')
            val url = URL("$cleanBase$path")
            conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = 25_000
                readTimeout = 60_000
                useCaches = false
                setRequestProperty("Accept", "application/json")
                if (!token.isNullOrBlank()) {
                    setRequestProperty("Authorization", "Bearer $token")
                }
                if (body != null) {
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                    OutputStreamWriter(outputStream, Charsets.UTF_8).use { writer ->
                        writer.write(body.toString())
                        writer.flush()
                    }
                }
            }

            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val raw = if (stream != null) {
                BufferedReader(InputStreamReader(stream, Charsets.UTF_8)).use { it.readText() }
            } else {
                "{}"
            }

            val parsed = try {
                JSONObject(raw)
            } catch (_: Exception) {
                JSONObject().put("raw", raw)
            }

            if (code in 200..299) {
                ApiResult(true, code, parsed)
            } else {
                val msg = parsed.optString("error", "Request failed (HTTP $code)")
                ApiResult(false, code, parsed, msg)
            }
        } catch (_: SocketTimeoutException) {
            ApiResult(
                ok = false,
                statusCode = 0,
                json = JSONObject(),
                errorMessage = "Server is waking up from sleep. Please tap again in a few seconds."
            )
        } catch (e: Exception) {
            ApiResult(
                ok = false,
                statusCode = 0,
                json = JSONObject(),
                errorMessage = "Connection failed: ${e.message ?: "Check internet or server status"}"
            )
        } finally {
            conn?.disconnect()
        }
    }
}
