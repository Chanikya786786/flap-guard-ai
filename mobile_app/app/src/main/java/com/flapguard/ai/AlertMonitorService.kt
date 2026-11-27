package com.flapguard.ai

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.content.ContextCompat
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * Opt-in On-Call Foreground Service for surgeons needing near-real-time (30s)
 * free-flap compromise alerts while the phone screen is locked or another app is open.
 */
class AlertMonitorService : Service() {

    private var scheduler: ScheduledExecutorService? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }

        val token = SecurePrefs.getAuthToken(this)
        if (token.isBlank()) {
            stopSelf()
            return START_NOT_STICKY
        }

        val ongoingNotif = NotificationHelper.buildOngoingMonitorNotification(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                ONGOING_NOTIF_ID,
                ongoingNotif,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            )
        } else {
            startForeground(ONGOING_NOTIF_ID, ongoingNotif)
        }

        if (scheduler == null) {
            scheduler = Executors.newSingleThreadScheduledExecutor().apply {
                scheduleWithFixedDelay(
                    {
                        val activeToken = SecurePrefs.getAuthToken(applicationContext)
                        if (activeToken.isBlank()) {
                            stopSelf()
                        } else {
                            AlertJobService.pollAlertsAndFlushQueue(applicationContext, activeToken)
                        }
                    },
                    5L,
                    30L,
                    TimeUnit.SECONDS
                )
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        scheduler?.shutdownNow()
        scheduler = null
    }

    companion object {
        private const val ONGOING_NOTIF_ID = 9901
        private const val ACTION_STOP = "com.flapguard.ai.STOP_MONITOR"
        private const val PREF_BG_ENABLED = "on_call_bg_monitor_enabled"

        fun isEnabled(context: Context): Boolean {
            return context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
                .getBoolean(PREF_BG_ENABLED, false)
        }

        fun setEnabled(context: Context, enabled: Boolean) {
            context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putBoolean(PREF_BG_ENABLED, enabled)
                .apply()
            if (enabled) start(context) else stop(context)
        }

        fun start(context: Context) {
            try {
                val intent = Intent(context, AlertMonitorService::class.java)
                ContextCompat.startForegroundService(context, intent)
            } catch (_: Exception) {
            }
        }

        fun stop(context: Context) {
            try {
                context.stopService(Intent(context, AlertMonitorService::class.java))
            } catch (_: Exception) {
            }
        }
    }
}
