package asia.lingtong.agent.data

import android.content.Context

const val DEFAULT_SERVER = "https://agent.lingtong.asia/agent"

data class Account(val id: Long, val username: String, val isAdmin: Boolean)

/** 面板账号与令牌的本地存储，全部经过 Keystore 加密。 */
class SessionStore(context: Context) {

    private val prefs = SecurePrefs(context, "session")

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER)?.takeIf { it.isNotBlank() } ?: DEFAULT_SERVER
        set(value) {
            prefs.putString(KEY_SERVER, value.trim().trimEnd('/'))
        }

    var accessToken: String?
        get() = prefs.getString(KEY_ACCESS)
        set(value) = prefs.putString(KEY_ACCESS, value)

    var refreshToken: String?
        get() = prefs.getString(KEY_REFRESH)
        set(value) = prefs.putString(KEY_REFRESH, value)

    var account: Account?
        get() {
            val username = prefs.getString(KEY_USERNAME) ?: return null
            val id = prefs.getString(KEY_USER_ID)?.toLongOrNull() ?: 0L
            val admin = prefs.getString(KEY_IS_ADMIN) == "1"
            return Account(id, username, admin)
        }
        set(value) {
            if (value == null) {
                prefs.putString(KEY_USERNAME, null)
                prefs.putString(KEY_USER_ID, null)
                prefs.putString(KEY_IS_ADMIN, null)
            } else {
                prefs.putString(KEY_USERNAME, value.username)
                prefs.putString(KEY_USER_ID, value.id.toString())
                prefs.putString(KEY_IS_ADMIN, if (value.isAdmin) "1" else "0")
            }
        }

    val loggedIn: Boolean
        get() = !accessToken.isNullOrBlank()

    fun saveAccount(value: Account) {
        account = value
    }

    fun saveTokens(access: String, refresh: String) {
        accessToken = access
        refreshToken = refresh
    }

    fun clear() {
        accessToken = null
        refreshToken = null
        account = null
    }

    private companion object {
        const val KEY_SERVER = "server_url"
        const val KEY_ACCESS = "access_token"
        const val KEY_REFRESH = "refresh_token"
        const val KEY_USERNAME = "username"
        const val KEY_USER_ID = "user_id"
        const val KEY_IS_ADMIN = "is_admin"
    }
}
