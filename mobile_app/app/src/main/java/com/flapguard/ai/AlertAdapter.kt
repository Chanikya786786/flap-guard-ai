package com.flapguard.ai

import android.content.res.ColorStateList
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

class AlertAdapter(
    private val onAcknowledge: (AlertItem) -> Unit,
    private val onCardClick: (AlertItem) -> Unit = {}
) : ListAdapter<AlertItem, AlertAdapter.AlertViewHolder>(DIFF) {

    override fun submitList(list: List<AlertItem>?) {
        super.submitList(list?.toList())
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): AlertViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_alert_card, parent, false)
        return AlertViewHolder(view)
    }

    private fun formatTimestamp(iso: String): String {
        if (iso.isBlank()) return ""
        return try {
            val normalized = if (iso.endsWith("Z") || iso.contains("+")) iso else "${iso}Z"
            val instant = try {
                Instant.parse(normalized)
            } catch (_: Exception) {
                OffsetDateTime.parse(normalized).toInstant()
            }
            val now = Instant.now()
            val mins = Duration.between(instant, now).toMinutes()
            val localDt = instant.atZone(ZoneId.systemDefault())
            val timeStr = DateTimeFormatter.ofPattern("HH:mm", Locale.US).format(localDt)
            when {
                mins < 1 -> "Just now · $timeStr"
                mins < 60 -> "${mins}m ago · $timeStr"
                mins < 24 * 60 -> "${mins / 60}h ago · $timeStr"
                else -> DateTimeFormatter.ofPattern("dd MMM · HH:mm", Locale.US).format(localDt)
            }
        } catch (_: Exception) {
            ""
        }
    }

    override fun onBindViewHolder(holder: AlertViewHolder, position: Int) {
        val item = getItem(position)
        val ctx = holder.itemView.context
        val density = ctx.resources.displayMetrics.density

        holder.tvPatient.text = item.patientName

        val formattedTime = formatTimestamp(item.createdAtIso)
        holder.tvTime.visibility = View.VISIBLE
        holder.tvTime.text = if (formattedTime.isNotBlank()) {
            "$formattedTime  ·  Open chart"
        } else {
            "Open patient chart"
        }

        val cleanMsg = item.message
            .replace(Regex("""^(URGENT|WARNING|WATCH|MONITOR)\s*\[[^\]]+\]:\s*""", RegexOption.IGNORE_CASE), "")
            .trim()
        holder.tvMessage.text = cleanMsg.ifBlank { item.message }

        holder.itemView.setOnClickListener { onCardClick(item) }

        if (item.acknowledged) {
            val colOk = ContextCompat.getColor(ctx, R.color.fg_success)
            val colOkBg = ContextCompat.getColor(ctx, R.color.fg_success_bg)
            holder.viewAccent.backgroundTintList = ColorStateList.valueOf(colOk)
            holder.tvTier.text = if (item.groupedCount > 1) "Reviewed ×${item.groupedCount}" else "Reviewed"
            holder.tvTier.backgroundTintList = ColorStateList.valueOf(colOkBg)
            holder.tvTier.setTextColor(colOk)
            holder.card.strokeColor = ContextCompat.getColor(ctx, R.color.fg_border)
            holder.card.strokeWidth = (1f * density).toInt()
            holder.btnAck.visibility = View.GONE
            holder.tvAckedBy.visibility = View.VISIBLE
            holder.tvAckedBy.text = if (item.groupedCount > 1) {
                "Reviewed by ${item.acknowledgedBy}  ·  ${item.groupedCount} similar events"
            } else {
                "Reviewed by ${item.acknowledgedBy}"
            }
        } else {
            val isCrit = item.riskTier == "Critical"
            val accent = ContextCompat.getColor(ctx, if (isCrit) R.color.fg_danger else R.color.fg_warning)
            val bg = ContextCompat.getColor(ctx, if (isCrit) R.color.fg_danger_bg else R.color.fg_warning_bg)
            holder.viewAccent.backgroundTintList = ColorStateList.valueOf(accent)
            holder.tvTier.text = item.riskTier
            holder.tvTier.backgroundTintList = ColorStateList.valueOf(bg)
            holder.tvTier.setTextColor(accent)
            holder.card.strokeColor = ContextCompat.getColor(
                ctx,
                if (isCrit) R.color.fg_danger_border else R.color.fg_border
            )
            holder.card.strokeWidth = ((if (isCrit) 1.5f else 1f) * density).toInt()
            holder.btnAck.visibility = View.VISIBLE
            holder.btnAck.setTextColor(accent)
            holder.btnAck.backgroundTintList = ColorStateList.valueOf(bg)
            holder.tvAckedBy.visibility = View.GONE
            holder.btnAck.setOnClickListener { onAcknowledge(item) }
        }
    }

    companion object {
        private val DIFF = object : DiffUtil.ItemCallback<AlertItem>() {
            override fun areItemsTheSame(oldItem: AlertItem, newItem: AlertItem): Boolean =
                oldItem.id == newItem.id && oldItem.acknowledged == newItem.acknowledged

            override fun areContentsTheSame(oldItem: AlertItem, newItem: AlertItem): Boolean =
                oldItem == newItem
        }
    }

    class AlertViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val card: MaterialCardView = view.findViewById(R.id.card_alert)
        val viewAccent: View = view.findViewById(R.id.view_alert_accent)
        val tvPatient: TextView = view.findViewById(R.id.tv_alert_patient)
        val tvTime: TextView = view.findViewById(R.id.tv_alert_time)
        val tvTier: TextView = view.findViewById(R.id.tv_alert_tier)
        val tvMessage: TextView = view.findViewById(R.id.tv_alert_message)
        val btnAck: MaterialButton = view.findViewById(R.id.btn_ack_alert)
        val tvAckedBy: TextView = view.findViewById(R.id.tv_alert_acked_by)
    }
}
