package com.flapguard.ai

import android.content.res.ColorStateList
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import java.util.Locale

class PatientAdapter(
    private val onCardClick: (PatientItem) -> Unit,
    private val onAssessClick: (PatientItem) -> Unit = {}
) : ListAdapter<PatientItem, PatientAdapter.PatientViewHolder>(DIFF) {

    override fun submitList(list: List<PatientItem>?) {
        super.submitList(list?.toList())
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): PatientViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_patient_card, parent, false)
        return PatientViewHolder(view)
    }

    override fun onBindViewHolder(holder: PatientViewHolder, position: Int) {
        val item = getItem(position)
        val ctx = holder.itemView.context
        val density = ctx.resources.displayMetrics.density

        holder.tvName.text = item.fullName
        holder.tvMrn.text = String.format(Locale.US, "%s  •  %dy  •  %.1fh post-op", item.mrn, item.age, item.hoursPostOp)
        holder.tvFlap.text = "${item.flapType} — ${item.surgerySite}"

        val (badgeLabel, badgeBgRes, badgeFgRes, barRes) = when (item.riskTier) {
            "Critical" -> Quad("Critical", R.color.fg_danger_bg, R.color.fg_danger, R.color.fg_danger)
            "High" -> Quad("High Risk", R.color.fg_warning_bg, R.color.fg_warning, R.color.fg_warning)
            "Moderate" -> Quad("Moderate", R.color.fg_info_bg, R.color.fg_info, R.color.fg_info)
            "Low" -> Quad("Healthy", R.color.fg_success_bg, R.color.fg_success, R.color.fg_success)
            else -> Quad("New", R.color.fg_surface_soft, R.color.fg_text_muted, R.color.fg_border_strong)
        }

        val badgeBg = ContextCompat.getColor(ctx, badgeBgRes)
        val badgeFg = ContextCompat.getColor(ctx, badgeFgRes)
        val barCol = ContextCompat.getColor(ctx, barRes)

        holder.tvBadge.text = badgeLabel
        holder.tvBadge.backgroundTintList = ColorStateList.valueOf(badgeBg)
        holder.tvBadge.setTextColor(badgeFg)
        holder.viewStatusBar.backgroundTintList = ColorStateList.valueOf(barCol)

        if (item.riskTier == "Critical") {
            holder.card.strokeColor = ContextCompat.getColor(ctx, R.color.fg_danger_border)
            holder.card.strokeWidth = (1.5f * density).toInt()
        } else {
            holder.card.strokeColor = ContextCompat.getColor(ctx, R.color.fg_border)
            holder.card.strokeWidth = (1f * density).toInt()
        }

        if (item.checkScheduleLabel.isNotBlank()) {
            holder.tvCheckSchedule.visibility = View.VISIBLE
            holder.tvCheckSchedule.text = item.checkScheduleLabel
            val schedBg = ContextCompat.getColor(
                ctx,
                if (item.checkOverdue) R.color.fg_danger_bg else R.color.fg_accent_soft
            )
            val schedFg = ContextCompat.getColor(
                ctx,
                if (item.checkOverdue) R.color.fg_danger else R.color.fg_accent
            )
            holder.tvCheckSchedule.backgroundTintList = ColorStateList.valueOf(schedBg)
            holder.tvCheckSchedule.setTextColor(schedFg)
        } else {
            holder.tvCheckSchedule.visibility = View.GONE
        }

        holder.tvOodBadge.visibility = if (item.oodFlag) View.VISIBLE else View.GONE

        val colNormal = ContextCompat.getColor(ctx, R.color.fg_text_primary)
        val colDanger = ContextCompat.getColor(ctx, R.color.fg_danger)

        if (item.tempDeltaC != null && item.spo2Pct != null && item.dopplerCmS != null) {
            holder.layoutVitalsStrip.visibility = View.VISIBLE
            holder.tvVitals.visibility = View.GONE
            holder.tvMetricDelta.text = String.format(Locale.US, "%.1f°C", item.tempDeltaC)
            holder.tvMetricSpo2.text = String.format(Locale.US, "%.1f%%", item.spo2Pct)
            holder.tvMetricDoppler.text = String.format(Locale.US, "%.1f cm/s", item.dopplerCmS)

            holder.tvMetricDelta.setTextColor(if (item.tempDeltaC >= 1.8) colDanger else colNormal)
            holder.tvMetricSpo2.setTextColor(if (item.spo2Pct < 90.0) colDanger else colNormal)
            holder.tvMetricDoppler.setTextColor(if (item.dopplerCmS < 12.0) colDanger else colNormal)
        } else {
            holder.layoutVitalsStrip.visibility = View.GONE
            holder.tvVitals.visibility = View.VISIBLE
            holder.tvVitals.text = "Awaiting initial flap assessment — tap to open chart"
        }

        holder.btnCardAssess.setOnClickListener { onAssessClick(item) }
        holder.card.setOnClickListener { onCardClick(item) }
    }

    private data class Quad(val a: String, val b: Int, val c: Int, val d: Int)

    companion object {
        private val DIFF = object : DiffUtil.ItemCallback<PatientItem>() {
            override fun areItemsTheSame(oldItem: PatientItem, newItem: PatientItem): Boolean =
                oldItem.id == newItem.id

            override fun areContentsTheSame(oldItem: PatientItem, newItem: PatientItem): Boolean =
                oldItem == newItem
        }
    }

    class PatientViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val card: MaterialCardView = view.findViewById(R.id.card_patient)
        val viewStatusBar: View = view.findViewById(R.id.view_status_bar)
        val tvName: TextView = view.findViewById(R.id.tv_patient_name)
        val tvMrn: TextView = view.findViewById(R.id.tv_patient_mrn)
        val tvBadge: TextView = view.findViewById(R.id.tv_risk_badge)
        val tvFlap: TextView = view.findViewById(R.id.tv_patient_flap)
        val tvCheckSchedule: TextView = view.findViewById(R.id.tv_check_schedule)
        val tvOodBadge: TextView = view.findViewById(R.id.tv_card_ood_badge)
        val layoutVitalsStrip: LinearLayout = view.findViewById(R.id.layout_vitals_strip)
        val tvMetricDelta: TextView = view.findViewById(R.id.tv_metric_delta)
        val tvMetricSpo2: TextView = view.findViewById(R.id.tv_metric_spo2)
        val tvMetricDoppler: TextView = view.findViewById(R.id.tv_metric_doppler)
        val btnCardAssess: MaterialButton = view.findViewById(R.id.btn_card_assess)
        val tvVitals: TextView = view.findViewById(R.id.tv_patient_vitals)
    }
}
