package com.flapguard.ai

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONObject

data class QueuedReading(
    val id: Long,
    val patientId: Int,
    val patientName: String,
    val payloadJson: String,
    val createdAtMs: Long
)

class OfflineQueueDb(context: Context) : SQLiteOpenHelper(
    context.applicationContext,
    DB_NAME,
    null,
    DB_VERSION
) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS queued_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                patient_name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL
            )
            """.trimIndent()
        )
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        onCreate(db)
    }

    fun enqueue(patientId: Int, patientName: String, payload: JSONObject): Long {
        val raw = payload.toString()
        val sealed = try {
            SecurePrefs.encryptString(raw)
        } catch (_: Exception) {
            raw
        }
        val sealedName = try {
            SecurePrefs.encryptString(patientName)
        } catch (_: Exception) {
            patientName
        }
        val values = ContentValues().apply {
            put("patient_id", patientId)
            put("patient_name", sealedName)
            put("payload_json", sealed)
            put("created_at_ms", System.currentTimeMillis())
        }
        return writableDatabase.insert("queued_readings", null, values)
    }

    fun getAll(): List<QueuedReading> {
        val list = mutableListOf<QueuedReading>()
        readableDatabase.rawQuery(
            "SELECT id, patient_id, patient_name, payload_json, created_at_ms FROM queued_readings ORDER BY id ASC",
            null
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val rawName = cursor.getString(2)
                val rawPayload = cursor.getString(3)
                val decName = if (rawName.startsWith("ENC_GCM_V1:")) {
                    SecurePrefs.decryptString(rawName) ?: "Patient"
                } else {
                    rawName
                }
                val decPayload = if (rawPayload.startsWith("ENC_GCM_V1:")) {
                    SecurePrefs.decryptString(rawPayload) ?: continue
                } else {
                    rawPayload
                }
                list.add(
                    QueuedReading(
                        id = cursor.getLong(0),
                        patientId = cursor.getInt(1),
                        patientName = decName,
                        payloadJson = decPayload,
                        createdAtMs = cursor.getLong(4)
                    )
                )
            }
        }
        return list
    }

    fun deleteById(id: Long) {
        writableDatabase.delete("queued_readings", "id = ?", arrayOf(id.toString()))
    }

    fun count(): Int {
        readableDatabase.rawQuery("SELECT COUNT(*) FROM queued_readings", null).use { c ->
            return if (c.moveToFirst()) c.getInt(0) else 0
        }
    }

    fun flushAll(serverUrl: String, authToken: String): Pair<Int, Int> {
        if (authToken.isBlank()) return 0 to count()
        val items = getAll()
        if (items.isEmpty()) return 0 to 0

        var synced = 0
        for (item in items) {
            val payload = try {
                JSONObject(item.payloadJson)
            } catch (_: Exception) {
                deleteById(item.id)
                continue
            }
            val res = ApiClient.request(
                serverUrl,
                "/mobile/patients/${item.patientId}/reading",
                "POST",
                payload,
                authToken
            )
            if (res.ok || res.statusCode == 400 || res.statusCode == 404) {
                deleteById(item.id)
                if (res.ok) synced++
            } else {
                break
            }
        }
        return synced to count()
    }

    companion object {
        private const val DB_NAME = "flapguard_offline_queue.db"
        private const val DB_VERSION = 1
    }
}
