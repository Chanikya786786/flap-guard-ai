package com.flapguard.ai

import android.content.res.ColorStateList
import android.graphics.Color
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.card.MaterialCardView
import java.util.Locale

class PatientAdapter(
    private val onCardClick: (PatientItem) -> Unit
) : RecyclerView.Adapter<PatientAdapter.PatientViewHolder>() {

    private val items = mutableListOf<PatientItem>()

    fun submitList(newItems: List<PatientItem>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): PatientViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_patient_card, parent, false)
        return PatientViewHolder(view)
    }

    override fun onBindViewHolder(holder: PatientViewHolder, position: Int) {
        val item = items[position]
        holder.tvName.text = item.fullName
        holder.tvFlap.text = String.format(
            Locale.US,
            "%s  •  %.1fh post-op",
            item.flapType,
            item.hoursPostOp
        )

        val (badgeLabel, badgeBg, badgeFg, barColor) = when (item.riskTier) {
            "Critical" -> Quad("Critical", "#FFF1F2", "#E11D48", "#E11D48")
            "High" -> Quad("High Risk", "#FFFBEB", "#D97706", "#D97706")
            "Moderate" -> Quad("Moderate", "#EFF6FF", "#2563EB", "#2563EB")
            "Low" -> Quad("Healthy", "#ECFDF5", "#059669", "#059669")
            else -> Quad("New", "#F1F5F9", "#64748B", "#CBD5E1")
        }

        holder.tvBadge.text = badgeLabel
        holder.tvBadge.backgroundTintList = ColorStateList.valueOf(Color.parseColor(badgeBg))
        holder.tvBadge.setTextColor(Color.parseColor(badgeFg))
        holder.viewStatusBar.backgroundTintList = ColorStateList.valueOf(Color.parseColor(barColor))

        if (item.tempDeltaC != null && item.spo2Pct != null && item.dopplerCmS != null) {
            holder.layoutVitalsStrip.visibility = View.VISIBLE
            holder.tvVitals.visibility = View.GONE
            holder.tvMetricDelta.text = String.format(Locale.US, "%.1f°C", item.tempDeltaC)
            holder.tvMetricSpo2.text = String.format(Locale.US, "%.1f%%", item.spo2Pct)
            holder.tvMetricDoppler.text = String.format(Locale.US, "%.1f", item.dopplerCmS)

            holder.tvMetricDelta.setTextColor(
                Color.parseColor(if (item.tempDeltaC >= 1.8) "#E11D48" else "#0F172A")
            )
            holder.tvMetricSpo2.setTextColor(
                Color.parseColor(if (item.spo2Pct < 90.0) "#E11D48" else "#0F172A")
            )
            holder.tvMetricDoppler.setTextColor(
                Color.parseColor(if (item.dopplerCmS < 12.0) "#E11D48" else "#0F172A")
            )
        } else {
            holder.layoutVitalsStrip.visibility = View.GONE
            holder.tvVitals.visibility = View.VISIBLE
            holder.tvVitals.text = "Tap to start first flap assessment"
        }

        holder.card.setOnClickListener { onCardClick(item) }
    }

    override fun getItemCount(): Int = items.size

    private data class Quad(val a: String, val b: String, val c: String, val d: String)

    class PatientViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val card: MaterialCardView = view.findViewById(R.id.card_patient)
        val viewStatusBar: View = view.findViewById(R.id.view_status_bar)
        val tvName: TextView = view.findViewById(R.id.tv_patient_name)
        val tvBadge: TextView = view.findViewById(R.id.tv_risk_badge)
        val tvFlap: TextView = view.findViewById(R.id.tv_patient_flap)
        val layoutVitalsStrip: LinearLayout = view.findViewById(R.id.layout_vitals_strip)
        val tvMetricDelta: TextView = view.findViewById(R.id.tv_metric_delta)
        val tvMetricSpo2: TextView = view.findViewById(R.id.tv_metric_spo2)
        val tvMetricDoppler: TextView = view.findViewById(R.id.tv_metric_doppler)
        val tvVitals: TextView = view.findViewById(R.id.tv_patient_vitals)
    }
}
