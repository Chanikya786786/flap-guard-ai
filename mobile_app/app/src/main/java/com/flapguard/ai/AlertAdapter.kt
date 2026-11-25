package com.flapguard.ai

import android.content.res.ColorStateList
import android.graphics.Color
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import java.util.Locale

class AlertAdapter(
    private val onAcknowledge: (AlertItem) -> Unit
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

    override fun onBindViewHolder(holder: AlertViewHolder, position: Int) {
        val item = items[position]
        holder.tvPatient.text = "${item.patientName}  (#${item.id})"
        holder.tvMessage.text = item.message
        holder.tvTier.text = item.riskTier.uppercase(Locale.US)

        if (item.acknowledged) {
            holder.card.setCardBackgroundColor(Color.WHITE)
            holder.card.strokeColor = Color.parseColor("#E2E8F0")
            holder.tvTier.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#DCFCE7"))
            holder.tvTier.setTextColor(Color.parseColor("#15803D"))
            holder.btnAck.visibility = View.GONE
            holder.tvAckedBy.visibility = View.VISIBLE
            holder.tvAckedBy.text = "Acknowledged by ${item.acknowledgedBy}"
        } else {
            holder.card.setCardBackgroundColor(Color.parseColor("#FEF2F2"))
            holder.card.strokeColor = Color.parseColor("#FCA5A5")
            holder.tvTier.backgroundTintList = ColorStateList.valueOf(Color.parseColor("#FEE2E2"))
            holder.tvTier.setTextColor(Color.parseColor("#B91C1C"))
            holder.btnAck.visibility = View.VISIBLE
            holder.tvAckedBy.visibility = View.GONE
            holder.btnAck.setOnClickListener { onAcknowledge(item) }
        }
    }

    override fun getItemCount(): Int = items.size

    class AlertViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val card: MaterialCardView = view.findViewById(R.id.card_alert)
        val tvPatient: TextView = view.findViewById(R.id.tv_alert_patient)
        val tvTier: TextView = view.findViewById(R.id.tv_alert_tier)
        val tvMessage: TextView = view.findViewById(R.id.tv_alert_message)
        val btnAck: MaterialButton = view.findViewById(R.id.btn_ack_alert)
        val tvAckedBy: TextView = view.findViewById(R.id.tv_alert_acked_by)
    }
}
