package com.flapguard.ai

import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

data class ApiResult(
    val ok: Boolean,
    val statusCode: Int,
    val json: JSONObject,
    val errorMessage: String = ""
)

object ApiClient {

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
                connectTimeout = 8000
                readTimeout = 15000
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
        } catch (e: Exception) {
            ApiResult(
                ok = false,
                statusCode = 0,
                json = JSONObject(),
                errorMessage = "Cannot reach server: ${e.message ?: "Check USB adb reverse or Server URL"}"
            )
        } finally {
            conn?.disconnect()
        }
    }
}