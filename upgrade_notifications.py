from pathlib import Path
import re
import xml.etree.ElementTree as ET

BASE = Path("mobile_app/app/src/main")


def write_file(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.lstrip("\n"), encoding="utf-8")


# 1. Monochrome white FlapGuard shield + pulse notification icon (Android guideline compliant)
write_file(
    BASE / "res/drawable/ic_stat_flapguard.xml",
    r'''
<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp"
    android:height="24dp"
    android:viewportWidth="24"
    android:viewportHeight="24">
    <path
        android:fillColor="#00000000"
        android:strokeColor="#FFFFFF"
        android:strokeWidth="1.9"
        android:strokeLineJoin="round"
        android:pathData="M12,2.5 L19.5,5.5 V11.5 C19.5,16.4 16.3,20.2 12,21.8 C7.7,20.2 4.5,16.4 4.5,11.5 V5.5 Z" />
    <path
        android:fillColor="#00000000"
        android:strokeColor="#FFFFFF"
        android:strokeWidth="1.9"
        android:strokeLineCap="round"
        android:strokeLineJoin="round"
        android:pathData="M6.8,12.4 H9.4 L10.8,8.8 L12.8,15.6 L14.4,11.4 L15.5,12.4 H17.4" />
</vector>
''',
)

# 2. AlertActionReceiver.kt (handles inline "Mark Reviewed" directly from notification shade)
write_file(
    BASE / "java/com/flapguard/ai/AlertActionReceiver.kt",
    r'''
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
''',
)

# 3. Safely patch AndroidManifest.xml (singleTop on DashboardActivity + register AlertActionReceiver)
manifest_path = BASE / "AndroidManifest.xml"
m_src = manifest_path.read_text(encoding="utf-8")

if 'android:name=".DashboardActivity"' not in m_src:
    raise RuntimeError("DashboardActivity entry not found in AndroidManifest.xml")

if 'android:launchMode="singleTop"' not in m_src:
    m_src = re.sub(
        r'(<activity\b[^>]*android:name="\.DashboardActivity")',
        r'\1\n            android:launchMode="singleTop"',
        m_src,
        count=1,
    )

if 'android:name=".AlertActionReceiver"' not in m_src:
    receiver_xml = '        <receiver\n            android:name=".AlertActionReceiver"\n            android:exported="false" />\n    </application>'
    if "</application>" not in m_src:
        raise RuntimeError("</application> tag missing in AndroidManifest.xml")
    m_src = m_src.replace("</application>", receiver_xml, 1)

manifest_path.write_text(m_src, encoding="utf-8")
ET.parse(manifest_path)

# 4. Patch DashboardActivity.kt (tap-to-open Alerts, rich clinical notification design, auto-cancel, live broadcast refresh)
dash_path = BASE / "java/com/flapguard/ai/DashboardActivity.kt"
d_src = dash_path.read_text(encoding="utf-8")

# Add required imports cleanly
import_additions = [
    "import android.app.PendingIntent",
    "import android.content.BroadcastReceiver",
    "import android.content.IntentFilter",
    "import android.graphics.Bitmap",
    "import android.graphics.Canvas",
    "import android.graphics.Paint",
    "import android.graphics.Typeface",
]
for imp in import_additions:
    if imp not in d_src:
        d_src = d_src.replace(
            "import android.app.NotificationManager",
            f"import android.app.NotificationManager\n{imp}",
            1,
        )

# Add BroadcastReceiver field for live refresh when user taps "Mark Reviewed" in notification shade
if "alertsUpdatedReceiver" not in d_src:
    old_poll = "    private val pollRunnable = object : Runnable {"
    new_poll = '''    private var receiverRegistered = false
    private val alertsUpdatedReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            lastAlertsSignature = ""
            fetchAlerts(silent = false)
            fetchPatients()
        }
    }

    private val pollRunnable = object : Runnable {'''
    if old_poll not in d_src:
        raise RuntimeError("Could not find pollRunnable in DashboardActivity.kt")
    d_src = d_src.replace(old_poll, new_poll, 1)

# Register receiver + handle launch intent extra in onCreate + add onNewIntent
old_sync_call = "        syncAll()\n    }"
new_sync_call = '''        ContextCompat.registerReceiver(
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
    }'''
if "handleNotificationIntent" not in d_src:
    if old_sync_call not in d_src:
        raise RuntimeError("Could not find syncAll() end of onCreate in DashboardActivity.kt")
    d_src = d_src.replace(old_sync_call, new_sync_call, 1)

# Unregister receiver cleanly in onDestroy
old_destroy = "        mainHandler.removeCallbacks(pollRunnable)\n        executor.shutdownNow()"
new_destroy = '''        mainHandler.removeCallbacks(pollRunnable)
        if (receiverRegistered) {
            try {
                unregisterReceiver(alertsUpdatedReceiver)
            } catch (_: Exception) {
            }
            receiverRegistered = false
        }
        executor.shutdownNow()'''
if "unregisterReceiver(alertsUpdatedReceiver)" not in d_src:
    if old_destroy not in d_src:
        raise RuntimeError("Could not find onDestroy block in DashboardActivity.kt")
    d_src = d_src.replace(old_destroy, new_destroy, 1)

# Pass predicted_class and risk_score_pct into pushSurgeonNotification
old_notify_call = '''                    if (!acked && notifiedAlertIds.add(id)) {
                        pushSurgeonNotification(id, "$tier Flap Alert: $pName", msg)
                    }'''
new_notify_call = '''                    val predClass = a.optString("predicted_class", "")
                    val riskPct = a.optDouble("risk_score_pct", -1.0)
                    if (!acked && notifiedAlertIds.add(id)) {
                        pushSurgeonNotification(id, pName, tier, predClass, riskPct, msg)
                    }'''
if old_notify_call not in d_src:
    raise RuntimeError("Could not find pushSurgeonNotification call site in DashboardActivity.kt")
d_src = d_src.replace(old_notify_call, new_notify_call, 1)

# Auto-dismiss Android notification when acknowledged inside the app
old_ack_ok = '                    showNotice("Alert marked reviewed.")'
new_ack_ok = '''                    (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(readingId)
                    showNotice("Alert marked reviewed.")'''
if old_ack_ok in d_src and ".cancel(readingId)" not in d_src:
    d_src = d_src.replace(old_ack_ok, new_ack_ok, 1)

old_ack_all = '                ApiClient.request(serverUrl(), "/mobile/alerts/$id/acknowledge", "POST", JSONObject(), authToken)'
new_ack_all = '''                val ackRes = ApiClient.request(serverUrl(), "/mobile/alerts/$id/acknowledge", "POST", JSONObject(), authToken)
                if (ackRes.ok) {
                    (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(id)
                }'''
if old_ack_all in d_src:
    d_src = d_src.replace(old_ack_all, new_ack_all, 1)

# Replace pushSurgeonNotification with rich, modern, clickable, interactive notification builder
old_fn_start = "    private fun pushSurgeonNotification("
if old_fn_start not in d_src:
    raise RuntimeError("Could not locate pushSurgeonNotification method.")

prefix = d_src[: d_src.index(old_fn_start)]
new_fn = '''    private fun buildInitialsBadgeBitmap(name: String, isCritical: Boolean): Bitmap {
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
'''

dash_path.write_text(prefix + new_fn, encoding="utf-8")

# Validate all Android XML files
for xml_file in (BASE / "res").rglob("*.xml"):
    ET.parse(xml_file)

print("Modern clickable FlapGuard notifications & Mark Reviewed receiver applied cleanly!")