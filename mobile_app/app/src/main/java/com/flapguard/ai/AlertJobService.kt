package com.flapguard.ai

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import java.util.concurrent.Executors

/**
 * Periodic Android JobScheduler worker (survives app close & Doze windows).
 * Polls urgent flap compromise alerts and flushes any offline queued readings.
 */
class AlertJobService : JobService() {

    private val executor = Executors.newSingleThreadExecutor()

    override fun onStartJob(params: JobParameters?): Boolean {
        val ctx = applicationContext
        val token = SecurePrefs.getAuthToken(ctx)
        if (token.isBlank()) {
            jobFinished(params, false)
            return false
        }

        executor.execute {
            try {
                pollAlertsAndFlushQueue(ctx, token)
            } finally {
                jobFinished(params, false)
            }
        }
        return true
    }

    override fun onStopJob(params: JobParameters?): Boolean = true

    override fun onDestroy() {
        super.onDestroy()
        executor.shutdownNow()
    }

    companion object {
        private const val JOB_ID = 4102

        fun schedule(context: Context) {
            val scheduler = context.getSystemService(Context.JOB_SCHEDULER_SERVICE) as? JobScheduler ?: return
            val component = ComponentName(context, AlertJobService::class.java)
            val info = JobInfo.Builder(JOB_ID, component)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPeriodic(15 * 60 * 1000L)
                .setPersisted(false)
                .build()
            scheduler.schedule(info)
        }

        fun cancel(context: Context) {
            val scheduler = context.getSystemService(Context.JOB_SCHEDULER_SERVICE) as? JobScheduler ?: return
            scheduler.cancel(JOB_ID)
        }

        fun pollAlertsAndFlushQueue(context: Context, token: String) {
            val baseUrl = ApiClient.getServerUrl(context)

            // 1. Flush offline queued bedside readings if connectivity returned
            val queueDb = OfflineQueueDb(context)
            val (synced, _) = queueDb.flushAll(baseUrl, token)
            if (synced > 0) {
                context.sendBroadcast(
                    Intent(AlertActionReceiver.ACTION_ALERTS_UPDATED).setPackage(context.packageName)
                )
            }

            // 2. Poll active surgeon alerts & fire lock-screen notification for new unacked alerts
            val res = ApiClient.request(baseUrl, "/mobile/alerts", "GET", null, token)
            if (!res.ok) return
            val arr = res.json.optJSONArray("alerts") ?: JSONArray()
            for (i in 0 until arr.length()) {
                val a = arr.getJSONObject(i)
                val id = a.optInt("id", -1)
                val acked = a.optBoolean("alert_acknowledged", false)
                if (id > 0 && !acked && NotificationHelper.markAndCheckShouldNotify(context, id)) {
                    NotificationHelper.showSurgeonAlert(
                        context = context,
                        id = id,
                        patientName = a.optString("patient_name", "Patient"),
                        riskTier = a.optString("risk_tier", "High"),
                        predictedClass = a.optString("predicted_class", ""),
                        riskScorePct = a.optDouble("risk_score_pct", -1.0),
                        rawMessage = a.optString("alert_message", "")
                    )
                }
            }
        }
    }
}
