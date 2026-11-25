package com.flapguard.ai

import android.content.res.ColorStateList
import android.graphics.Color
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import java.util.Locale

class PatientAdapter(
    private val onCardClick: (PatientItem) -> Unit,
    private val onAssessClick: (PatientItem) -> Unit,
    private val onDeleteClick: (PatientItem) -> Unit
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
        holder.tvMrn.text = String.format(
            Locale.US,
            "%s  •  %dy  •  %.1fh post-op",
            item.mrn,
            item.age,
            item.hoursPostOp
        )
        holder.tvFlap.text = "${item.flapType} — ${item.surgerySite}"

        if (item.tempDeltaC != null && item.spo2Pct != null && item.dopplerCmS != null) {
            holder.tvVitals.text = String.format(
                Locale.US,
                "AI: %s (%.0f%%)  •  ΔT %.1f°C  •  SpO₂ %.1f%%  •  Doppler %.1f cm/s",
                item.predictedClass,
                item.riskScorePct,
                item.tempDeltaC,
                item.spo2Pct,
                item.dopplerCmS
            )
        } else {
            holder.tvVitals.text = "Awaiting first postoperative flap assessment"
        }

        holder.tvBadge.text = item.riskTier.uppercase(Locale.US)
        val (badgeBg, badgeFg, strokeColor) = when (item.riskTier) {
            "Critical" -> Triple("#FEE2E2", "#B91C1C", "#FCA5A5")
            "High" -> Triple("#FEF3C7", "#B45309", "#FCD34D")
            "Moderate" -> Triple("#DBEAFE", "#1D4ED8", "#93C5FD")
            "Low" -> Triple("#DCFCE7", "#15803D", "#E2E8F0")
            else -> Triple("#F1F5F9", "#475569", "#E2E8F0")
        }
        holder.tvBadge.backgroundTintList = ColorStateList.valueOf(Color.parseColor(badgeBg))
        holder.tvBadge.setTextColor(Color.parseColor(badgeFg))
        holder.card.strokeColor = Color.parseColor(strokeColor)

        holder.card.setOnClickListener { onCardClick(item) }
        holder.btnAssess.setOnClickListener { onAssessClick(item) }
        holder.btnDelete.setOnClickListener { onDeleteClick(item) }
    }

    override fun getItemCount(): Int = items.size

    class PatientViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val card: MaterialCardView = view.findViewById(R.id.card_patient)
        val tvName: TextView = view.findViewById(R.id.tv_patient_name)
        val tvMrn: TextView = view.findViewById(R.id.tv_patient_mrn)
        val tvBadge: TextView = view.findViewById(R.id.tv_risk_badge)
        val tvFlap: TextView = view.findViewById(R.id.tv_patient_flap)
        val tvVitals: TextView = view.findViewById(R.id.tv_patient_vitals)
        val btnAssess: MaterialButton = view.findViewById(R.id.btn_card_assess)
        val btnDelete: ImageButton = view.findViewById(R.id.btn_card_delete)
    }
}
