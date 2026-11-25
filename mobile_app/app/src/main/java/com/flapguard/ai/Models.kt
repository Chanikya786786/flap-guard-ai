package com.flapguard.ai

data class PatientItem(
    val id: Int,
    val mrn: String,
    val fullName: String,
    val age: Int,
    val flapType: String,
    val surgerySite: String,
    val hoursPostOp: Double,
    val riskTier: String,
    val predictedClass: String,
    val riskScorePct: Double,
    val tempDeltaC: Double?,
    val spo2Pct: Double?,
    val dopplerCmS: Double?,
    val visualScore: Double?,
    val alertMessage: String
)

data class AlertItem(
    val id: Int,
    val patientName: String,
    val riskTier: String,
    val message: String,
    val acknowledged: Boolean,
    val acknowledgedBy: String
)
