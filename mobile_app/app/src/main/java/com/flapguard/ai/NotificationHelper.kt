package com.flapguard.ai

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import java.util.Locale

object NotificationHelper {

    const val ALERTS_CHANNEL_ID = "flap_alerts_channel"
    const val MONITOR_CHANNEL_ID = "flap_monitor_channel"
    private const val PREF_NOTIFIED_IDS = "notified_alert_ids_v3"

    fun ensureChannels(context: Context) {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val alertChannel = NotificationChannel(
            ALERTS_CHANNEL_ID,
            "FlapGuard Urgent Surgeon Alerts",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Real-time venous and arterial flap compromise alerts"
            enableVibration(true)
        }
        manager.createNotificationChannel(alertChannel)

        val monitorChannel = NotificationChannel(
            MONITOR_CHANNEL_ID,
            "On-Call Ward Monitoring",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Keeps postoperative flap alert checks active while screen is locked"
            setShowBadge(false)
        }
        manager.createNotificationChannel(monitorChannel)
    }

    fun initialsOf(name: String): String {
        val parts = name.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
        return when {
            parts.size >= 2 -> "${parts[0].first()}${parts[1].first()}"
            parts.size == 1 -> parts[0].take(2)
            else -> "FG"
        }.uppercase(Locale.US)
    }

    fun markAndCheckShouldNotify(context: Context, alertId: Int): Boolean {
        val prefs = context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
        val existing = prefs.getStringSet(PREF_NOTIFIED_IDS, emptySet())?.toMutableSet() ?: mutableSetOf()
        val key = alertId.toString()
        if (existing.contains(key)) return false
        existing.add(key)
        val trimmed = if (existing.size > 100) existing.toList().takeLast(80).toSet() else existing
        prefs.edit().putStringSet(PREF_NOTIFIED_IDS, trimmed).apply()
        return true
    }

    fun clearNotifiedHistory(context: Context) {
        context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(PREF_NOTIFIED_IDS)
            .apply()
    }

    fun cancelAlert(context: Context, alertId: Int) {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.cancel(alertId)
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

    fun showSurgeonAlert(
        context: Context,
        id: Int,
        patientName: String,
        riskTier: String,
        predictedClass: String,
        riskScorePct: Double,
        rawMessage: String
    ) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        ensureChannels(context)

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

        val openIntent = Intent(context, DashboardActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra("open_tab", "alerts")
            putExtra(AlertActionReceiver.EXTRA_ALERT_ID, id)
        }
        val openPendingIntent = PendingIntent.getActivity(
            context,
            id * 10 + 1,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val reviewIntent = Intent(context, AlertActionReceiver::class.java).apply {
            action = AlertActionReceiver.ACTION_MARK_REVIEWED
            putExtra(AlertActionReceiver.EXTRA_ALERT_ID, id)
        }
        val reviewPendingIntent = PendingIntent.getBroadcast(
            context,
            id * 10 + 2,
            reviewIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(context, ALERTS_CHANNEL_ID)
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

        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(id, notification)
    }

    fun buildOngoingMonitorNotification(context: Context): Notification {
        ensureChannels(context)
        val openIntent = Intent(context, DashboardActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val pi = PendingIntent.getActivity(
            context,
            9901,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(context, MONITOR_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_flapguard)
            .setColor(Color.parseColor("#0B3B66"))
            .setContentTitle("FlapGuard On-Call Monitor Active")
            .setContentText("Checking postoperative flap alerts in background")
            .setContentIntent(pi)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }
}
