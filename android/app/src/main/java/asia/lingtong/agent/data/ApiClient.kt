package asia.lingtong.agent.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()

class ApiException(val status: Int, message: String) : IOException(message)

data class DeviceInfo(
    val id: String,
    val deviceId: String,
    val name: String,
    val hardware: String,
    val fwVersion: String,
    val protocol: String,
    val online: Boolean,
    val claimedAt: String,
    val lastSeenAt: String,
) {
    val title: String get() = name.ifBlank { deviceId }
}

data class FirmwareRelease(
    val id: Long,
    val version: String,
    val hardware: String,
    val protocol: String,
    val notes: String,
    val sha256: String,
    val size: Long,
    val createdAt: String,
    val publishedAt: String?,
)

data class OTAEventInfo(
    val id: Long,
    val deviceId: String,
    val firmwareId: Long,
    val fwVersion: String,
    val event: String,
    val status: String,
    val detail: String,
    val createdAt: String,
)

data class ChatTurn(val role: String, val content: String)

data class MemoryItem(
    val id: Long,
    val fact: String,
    val source: String,
    val createdAt: String,
)

data class PushResult(
    val deviceId: String,
    val online: Boolean,
    val delivered: Boolean,
    val version: Int?,
    val error: String,
)

/** 机器人屏幕/音量状态；表情由固件按屏幕型号渲染。 */
data class PanelState(
    val deviceId: String,
    val online: Boolean,
    val delivered: Boolean,
    val volume: Int,
    val emotion: String,
    val text: String,
    val active: Boolean,
    val error: String,
)

class ApiClient(private val session: SessionStore) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private val refreshLock = Mutex()

    fun endpoint(path: String): String = session.serverUrl.trimEnd('/') + path

    suspend fun login(username: String, password: String): Account = withContext(Dispatchers.IO) {
        val body = JSONObject().put("username", username).put("password", password)
        val request = Request.Builder()
            .url(endpoint("/api/v1/auth/login"))
            .post(body.toString().toRequestBody(JSON_MEDIA))
            .build()
        val raw = client.newCall(request).execute().use { response ->
            Raw(response.code, response.body?.bytes() ?: ByteArray(0))
        }
        if (raw.code !in 200..299) throw errorOf(raw.code, raw.text)
        val json = JSONObject(raw.text)
        val user = json.optJSONObject("user")
        val account = Account(
            id = user?.optLong("id") ?: 0L,
            username = user?.optString("username") ?: username,
            isAdmin = user?.optBoolean("is_admin") ?: false,
        )
        session.saveAccount(account)
        session.saveTokens(json.getString("access_token"), json.getString("refresh_token"))
        account
    }

    suspend fun fetchDevices(): List<DeviceInfo> {
        val items = JSONObject(callJson("/api/v1/devices")).optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).map { parseDevice(items.getJSONObject(it)) }
    }

    suspend fun fetchFirmware(): List<FirmwareRelease> {
        val items = JSONObject(callJson("/api/v1/firmware")).optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).map {
            val item = items.getJSONObject(it)
            FirmwareRelease(
                id = item.optLong("id"),
                version = item.optString("version"),
                hardware = item.optString("hardware"),
                protocol = item.optString("protocol"),
                notes = item.optString("notes"),
                sha256 = item.optString("sha256"),
                size = item.optLong("size"),
                createdAt = item.optString("created_at"),
                publishedAt = if (item.isNull("published_at")) null else item.optString("published_at"),
            )
        }
    }

    suspend fun requestOta(deviceId: String, firmwareId: Long): OTAEventInfo =
        parseOtaEvent(
            JSONObject(
                callJson(
                    "/api/v1/devices/$deviceId/ota",
                    method = "POST",
                    json = JSONObject().put("firmware_id", firmwareId),
                )
            )
        )

    suspend fun fetchOtaEvents(deviceId: String): List<OTAEventInfo> {
        val items = JSONObject(
            callJson("/api/v1/devices/$deviceId/ota")
        ).optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).map { parseOtaEvent(items.getJSONObject(it)) }
    }

    /** 为蓝牙配网申请一次性绑定令牌。 */
    suspend fun createPairing(deviceId: String): String {
        val body = JSONObject().put("device_id", deviceId)
        val response = callJson("/api/v1/pairings", method = "POST", json = body)
        return JSONObject(response).optString("pairing_token")
    }

    /** 已绑定设备改网：签发一次性授权，不重新认领或更换设备身份。 */
    suspend fun createNetworkAuthorization(deviceId: String): String {
        val response = callJson(
            "/api/v1/devices/$deviceId/network_authorization",
            method = "POST",
        )
        return JSONObject(response).optString("pairing_token")
    }

    suspend fun pushConfig(deviceId: String, config: RobotConfig): PushResult {
        val body = callJson(
            "/api/v1/devices/$deviceId/config",
            method = "POST",
            json = config.toJson(),
        )
        return parsePush(body)
    }

    suspend fun configStatus(deviceId: String): PushResult =
        parsePush(callJson("/api/v1/devices/$deviceId/config/status"))

    suspend fun pullHistory(deviceId: String): List<ChatTurn> {
        val items = JSONObject(
            callJson("/api/v1/devices/$deviceId/history/pull", method = "POST")
        ).optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).map {
            val item = items.getJSONObject(it)
            ChatTurn(item.optString("role"), item.optString("content"))
        }
    }

    suspend fun ackHistory(deviceId: String, count: Int) {
        callJson("/api/v1/devices/$deviceId/history/ack?count=$count", method = "POST")
    }

    suspend fun panelState(deviceId: String): PanelState =
        parsePanel(callJson("/api/v1/devices/$deviceId/panel"))

    /** 下发屏幕表情/文字与音量；clear=true 时恢复默认状态界面。 */
    suspend fun sendPanel(
        deviceId: String,
        volume: Int? = null,
        emotion: String? = null,
        text: String = "",
        clear: Boolean = false,
    ): PanelState {
        val body = JSONObject().put("text", text).put("clear", clear)
        if (volume != null) body.put("volume", volume)
        if (emotion != null) body.put("emotion", emotion)
        return parsePanel(callJson("/api/v1/devices/$deviceId/panel", method = "POST", json = body))
    }

    suspend fun chatText(text: String, history: List<ChatTurn>): String {
        val body = JSONObject().put("text", text)
        if (history.isNotEmpty()) {
            val array = JSONArray()
            history.forEach { array.put(JSONObject().put("role", it.role).put("content", it.content)) }
            body.put("history", array)
        }
        return JSONObject(callJson("/api/v1/chat/text", method = "POST", json = body))
            .optString("reply")
    }

    suspend fun chatTts(text: String): ByteArray {
        val body = JSONObject().put("text", text)
        val raw = call("/api/v1/chat/tts", method = "POST", json = body)
        if (raw.code !in 200..299) throw errorOf(raw.code, raw.text)
        return raw.bytes
    }

    /** 用用户自己的 TTS 配置试听音色/语速，返回 WAV 字节。 */
    suspend fun ttsPreview(text: String, profile: ServiceProfile): ByteArray {
        val body = JSONObject()
            .put("text", text)
            .put("base_url", profile.baseUrl)
            .put("api_key", profile.apiKey)
            .put("model", profile.model)
            .put("voice", profile.voice)
            .put("speed", profile.speed.toDouble())
        val raw = call("/api/v1/chat/tts_preview", method = "POST", json = body)
        if (raw.code !in 200..299) throw errorOf(raw.code, raw.text)
        return raw.bytes
    }

    /** 上传音色样本做克隆，返回可直接填进 voice 的音色标识（服务商的 speech:xxx）。 */
    suspend fun uploadVoice(
        profile: ServiceProfile,
        name: String,
        fileName: String,
        mimeType: String,
        bytes: ByteArray,
        text: String = "",
    ): String = withContext(Dispatchers.IO) {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("base_url", profile.baseUrl)
            .addFormDataPart("api_key", profile.apiKey)
            .addFormDataPart("model", profile.model)
            .addFormDataPart("name", name)
            .apply { if (text.isNotBlank()) addFormDataPart("text", text) }
            .addFormDataPart(
                "file",
                fileName,
                bytes.toRequestBody(mimeType.toMediaTypeOrNull()),
            )
            .build()

        val usedToken = session.accessToken
        var response = executeMultipart("/api/v1/chat/voice_upload", body, usedToken)
        if (response.code == 401 && usedToken != null) {
            response.close()
            if (refreshTokens(usedToken)) {
                response = executeMultipart("/api/v1/chat/voice_upload", body, session.accessToken)
            }
        }
        val raw = response.use { it.toRaw() }
        if (raw.code !in 200..299) throw errorOf(raw.code, raw.text)
        JSONObject(raw.text).optString("voice")
    }

    /** 返回给界面直接展示的短结果。 */
    suspend fun testConnectivity(): String {
        val devices = fetchDevices()
        val online = devices.count { it.online }
        return "服务连通：${devices.size} 台设备，$online 台在线"
    }

    suspend fun fetchMemories(limit: Int = 100): List<MemoryItem> {
        val items = JSONObject(
            callJson("/api/v1/memory?limit=$limit")
        ).optJSONArray("items") ?: JSONArray()
        return (0 until items.length()).map {
            val item = items.getJSONObject(it)
            MemoryItem(
                id = item.optLong("id"),
                fact = item.optString("fact"),
                source = item.optString("source"),
                createdAt = item.optString("created_at"),
            )
        }
    }

    suspend fun deleteMemory(id: Long) {
        val raw = call("/api/v1/memory/$id", method = "DELETE")
        if (raw.code != 204 && raw.code !in 200..299) throw errorOf(raw.code, raw.text)
    }

    private suspend fun callJson(path: String, method: String = "GET", json: JSONObject? = null): String {
        val raw = call(path, method, json)
        if (raw.code !in 200..299) throw errorOf(raw.code, raw.text)
        return raw.text
    }

    private suspend fun call(path: String, method: String, json: JSONObject? = null): Raw =
        withContext(Dispatchers.IO) {
            val usedToken = session.accessToken
            var response = execute(path, method, json, usedToken)
            if (response.code == 401 && usedToken != null) {
                response.close()
                if (refreshTokens(usedToken)) {
                    response = execute(path, method, json, session.accessToken)
                }
            }
            response.use { it.toRaw() }
        }

    private fun execute(path: String, method: String, json: JSONObject?, token: String?): Response {
        val builder = Request.Builder().url(endpoint(path))
        val media = JSON_MEDIA
        when (method) {
            "POST" -> builder.post((json?.toString() ?: "{}").toRequestBody(media))
            "PUT" -> builder.put((json?.toString() ?: "{}").toRequestBody(media))
            "DELETE" -> builder.delete()
            else -> builder.get()
        }
        if (!token.isNullOrBlank()) builder.header("Authorization", "Bearer $token")
        return client.newCall(builder.build()).execute()
    }

    private fun executeMultipart(path: String, body: MultipartBody, token: String?): Response {
        val builder = Request.Builder().url(endpoint(path)).post(body)
        if (!token.isNullOrBlank()) builder.header("Authorization", "Bearer $token")
        return client.newCall(builder.build()).execute()
    }

    /** 并发请求只刷新一次；刷新失败就清掉登录态。 */
    private suspend fun refreshTokens(usedToken: String): Boolean = refreshLock.withLock {
        if (session.accessToken != usedToken) return@withLock true
        val refresh = session.refreshToken ?: return@withLock false
        val raw = runCatching {
            withContext(Dispatchers.IO) {
                val request = Request.Builder()
                    .url(endpoint("/api/v1/auth/refresh"))
                    .post(JSONObject().put("refresh_token", refresh).toString().toRequestBody(JSON_MEDIA))
                    .build()
                client.newCall(request).execute().use { it.toRaw() }
            }
        }.getOrNull() ?: return@withLock false
        if (raw.code !in 200..299) {
            session.clear()
            return@withLock false
        }
        val json = JSONObject(raw.text)
        session.saveTokens(json.getString("access_token"), json.getString("refresh_token"))
        true
    }

    private fun parseDevice(json: JSONObject) = DeviceInfo(
        id = json.optString("id"),
        deviceId = json.optString("device_id"),
        name = json.optString("name"),
        hardware = json.optString("hardware"),
        fwVersion = json.optString("fw_version"),
        protocol = json.optString("protocol"),
        online = json.optBoolean("online"),
        claimedAt = json.optString("claimed_at"),
        lastSeenAt = json.optString("last_seen_at"),
    )

    private fun parseOtaEvent(json: JSONObject) = OTAEventInfo(
        id = json.optLong("id"),
        deviceId = json.optString("device_id"),
        firmwareId = json.optLong("firmware_id"),
        fwVersion = json.optString("fw_version"),
        event = json.optString("event"),
        status = json.optString("status"),
        detail = json.optString("detail"),
        createdAt = json.optString("created_at"),
    )

    private fun parsePush(text: String): PushResult {
        val json = JSONObject(text)
        return PushResult(
            deviceId = json.optString("device_id"),
            online = json.optBoolean("online"),
            delivered = json.optBoolean("delivered"),
            version = if (json.isNull("version")) null else json.optInt("version"),
            error = json.optString("error"),
        )
    }

    private fun parsePanel(text: String): PanelState {
        val json = JSONObject(text)
        return PanelState(
            deviceId = json.optString("device_id"),
            online = json.optBoolean("online"),
            delivered = json.optBoolean("delivered"),
            volume = json.optInt("volume", 100).coerceIn(0, 100),
            emotion = json.optString("emotion", "neutral"),
            text = json.optString("text"),
            active = json.optBoolean("active"),
            error = json.optString("error"),
        )
    }

    private fun errorOf(code: Int, text: String): ApiException {
        val detail = runCatching { JSONObject(text).optString("detail") }.getOrNull()
        val message = detail?.takeIf { it.isNotBlank() } ?: "HTTP $code"
        return ApiException(code, message)
    }

}

private class Raw(val code: Int, val bytes: ByteArray) {
    val text: String get() = bytes.toString(Charsets.UTF_8)
}

private fun Response.toRaw(): Raw = Raw(code, body?.bytes() ?: ByteArray(0))
