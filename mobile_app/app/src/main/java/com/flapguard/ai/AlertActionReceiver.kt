package com.flapguard.ai

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONObject
import java.util.concurrent.Executors

class AlertActionReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_MARK_REVIEWED) return
        val alertId = intent.getIntExtra(EXTRA_ALERT_ID, -1)
        if (alertId <= 0) return

        val prefs = context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
        val token = prefs.getString("auth_token", "").orEmpty()
        if (token.isBlank()) return

        val pendingResult = goAsync()
        val appContext = context.applicationContext
        val serverUrl = ApiClient.getServerUrl(appContext)

        Executors.newSingleThreadExecutor().execute {
            try {
                val res = ApiClient.request(
                    serverUrl,
                    "/mobile/alerts/$alertId/acknowledge",
                    "POST",
                    JSONObject(),
                    token
                )
                if (res.ok) {
                    val manager = appContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                    manager.cancel(alertId)
                    val refresh = Intent(ACTION_ALERTS_UPDATED).setPackage(appContext.packageName)
                    appContext.sendBroadcast(refresh)
                }
            } finally {
                pendingResult.finish()
            }
        }
    }

    companion object {
        const val ACTION_MARK_REVIEWED = "com.flapguard.ai.ACTION_MARK_REVIEWED"
        const val ACTION_ALERTS_UPDATED = "com.flapguard.ai.ACTION_ALERTS_UPDATED"
        const val EXTRA_ALERT_ID = "alert_id"
    }
}
