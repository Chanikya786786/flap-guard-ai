package com.flapguard.ai

import android.content.Context
import androidx.appcompat.app.AppCompatDelegate
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Hardware-backed Android Keystore (AES-256-GCM) vault for bearer tokens,
 * encrypted offline SQLite payloads, and local device privacy preferences.
 */
object SecurePrefs {

    private const val KEYSTORE_PROVIDER = "AndroidKeyStore"
    private const val KEY_ALIAS = "flapguard_auth_aes256_key"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val ENC_PREFIX = "ENC_GCM_V1:"
    private const val ENC_TOKEN_KEY = "auth_token_enc"
    private const val LEGACY_TOKEN_KEY = "auth_token"
    private const val PREF_SCREEN_PRIVACY = "screen_privacy_secure_flag"
    private const val PREF_APP_LOCK = "device_biometric_app_lock"
    private const val PREF_THEME_MODE = "ui_theme_mode"

    private fun getOrCreateKey(): SecretKey {
        val ks = KeyStore.getInstance(KEYSTORE_PROVIDER).apply { load(null) }
        val existing = ks.getKey(KEY_ALIAS, null) as? SecretKey
        if (existing != null) return existing

        val spec = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build()

        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE_PROVIDER)
        gen.init(spec)
        return gen.generateKey()
    }

    fun encryptString(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        val iv = cipher.iv
        val ciphertext = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        val combined = ByteArray(1 + iv.size + ciphertext.size)
        combined[0] = iv.size.toByte()
        System.arraycopy(iv, 0, combined, 1, iv.size)
        System.arraycopy(ciphertext, 0, combined, 1 + iv.size, ciphertext.size)
        return ENC_PREFIX + Base64.encodeToString(combined, Base64.NO_WRAP)
    }

    fun decryptString(stored: String): String? {
        return try {
            val payload = if (stored.startsWith(ENC_PREFIX)) stored.removePrefix(ENC_PREFIX) else stored
            val combined = Base64.decode(payload, Base64.NO_WRAP)
            val ivLen = combined[0].toInt() and 0xFF
            val iv = combined.copyOfRange(1, 1 + ivLen)
            val ciphertext = combined.copyOfRange(1 + ivLen, combined.size)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(ciphertext), Charsets.UTF_8)
        } catch (_: Exception) {
            null
        }
    }

    fun saveAuthToken(context: Context, token: String) {
        val prefs = context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
        if (token.isBlank()) {
            clearAuthToken(context)
            return
        }
        try {
            val encoded = encryptString(token)
            prefs.edit()
                .putString(ENC_TOKEN_KEY, encoded)
                .remove(LEGACY_TOKEN_KEY)
                .apply()
        } catch (_: Exception) {
            prefs.edit().remove(ENC_TOKEN_KEY).remove(LEGACY_TOKEN_KEY).apply()
        }
    }

    fun getAuthToken(context: Context): String {
        val prefs = context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
        val encoded = prefs.getString(ENC_TOKEN_KEY, null)
        if (!encoded.isNullOrBlank()) {
            val decrypted = decryptString(encoded)
            if (decrypted != null) return decrypted
            clearAuthToken(context)
            return ""
        }
        val legacy = prefs.getString(LEGACY_TOKEN_KEY, null).orEmpty()
        if (legacy.isNotBlank()) {
            saveAuthToken(context, legacy)
            return legacy
        }
        return ""
    }

    fun clearAuthToken(context: Context) {
        context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(ENC_TOKEN_KEY)
            .remove(LEGACY_TOKEN_KEY)
            .apply()
    }

    fun isScreenPrivacyEnabled(context: Context): Boolean {
        return context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
            .getBoolean(PREF_SCREEN_PRIVACY, false)
    }

    fun setScreenPrivacyEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(PREF_SCREEN_PRIVACY, enabled)
            .apply()
    }

    fun isAppLockEnabled(context: Context): Boolean {
        return context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
            .getBoolean(PREF_APP_LOCK, false)
    }

    fun setAppLockEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(PREF_APP_LOCK, enabled)
            .apply()
    }

    fun getThemeMode(context: Context): String {
        return context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
            .getString(PREF_THEME_MODE, "system") ?: "system"
    }

    fun setThemeMode(context: Context, mode: String) {
        context.getSharedPreferences(ApiClient.PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(PREF_THEME_MODE, mode)
            .apply()
        applySavedTheme(context)
    }

    fun applySavedTheme(context: Context) {
        val target = when (getThemeMode(context)) {
            "light" -> AppCompatDelegate.MODE_NIGHT_NO
            "dark" -> AppCompatDelegate.MODE_NIGHT_YES
            else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
        }
        AppCompatDelegate.setDefaultNightMode(target)
    }
}
