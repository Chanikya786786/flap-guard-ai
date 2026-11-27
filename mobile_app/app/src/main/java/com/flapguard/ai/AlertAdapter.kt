package com.flapguard.ai

import android.content.res.ColorStateList
import android.graphics.Color
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.button.MaterialButton
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

class AlertAdapter(
    private val onAcknowledge: (AlertItem) -> Unit,
    private val onCardClick: (AlertItem) -> Unit = {}
) : RecyclerView.Adapter<AlertAdapter.AlertViewHolder>() {

    private val items = mutableListOf<AlertItem>()

    fun submitList(newItems: List<AlertItem>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
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
        val item = items[position]
        holder.tvPatient.text = item.patientName

        val formattedTime = formatTimestamp(item.createdAtIso)
        holder.tvTime.visibility = View.VISIBLE
        holder.tvTime.text = if (formattedTime.isNotBlank()) {
            "$formattedTime  ·  Tap to view trend"
        } else {
            "Tap to view patient trend"
        }

        val cleanMsg = item.message
            .replace(Regex("^URGENT\\s*\\[[^]]+]:\\s*"), "")
            .trim()
        holder.tvMessage.text = cleanMsg.ifBlank { item.message }

        holder.itemView.setOnClickListener { onCardClick(item) }

        if (item.acknowledged) {
            holder.viewAccent.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#059669"))
            holder.tvTier.text = "Reviewed"
            holder.tvTier.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#ECFDF5"))
            holder.tvTier.setTextColor(Color.parseColor("#059669"))
            holder.btnAck.visibility = View.GONE
            holder.tvAckedBy.visibility = View.VISIBLE
            holder.tvAckedBy.text = "Reviewed by ${item.acknowledgedBy}"
        } else {
            val accent = if (item.riskTier == "Critical") "#E11D48" else "#D97706"
            val bg = if (item.riskTier == "Critical") "#FFF1F2" else "#FFFBEB"
            holder.viewAccent.backgroundTintList = ColorStateList.valueOf(Color.parseColor(accent))
            holder.tvTier.text = item.riskTier
            holder.tvTier.backgroundTintList = ColorStateList.valueOf(Color.parseColor(bg))
            holder.tvTier.setTextColor(Color.parseColor(accent))
            holder.btnAck.visibility = View.VISIBLE
            holder.tvAckedBy.visibility = View.GONE
            holder.btnAck.setOnClickListener { onAcknowledge(item) }
        }
    }

    override fun getItemCount(): Int = items.size

    class AlertViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val viewAccent: View = view.findViewById(R.id.view_alert_accent)
        val tvPatient: TextView = view.findViewById(R.id.tv_alert_patient)
        val tvTime: TextView = view.findViewById(R.id.tv_alert_time)
        val tvTier: TextView = view.findViewById(R.id.tv_alert_tier)
        val tvMessage: TextView = view.findViewById(R.id.tv_alert_message)
        val btnAck: MaterialButton = view.findViewById(R.id.btn_ack_alert)
        val tvAckedBy: TextView = view.findViewById(R.id.tv_alert_acked_by)
    }
}
