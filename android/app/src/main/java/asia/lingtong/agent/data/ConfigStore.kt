package asia.lingtong.agent.data

import android.content.Context
import org.json.JSONObject

data class ServiceProfile(
    val baseUrl: String = "",
    val model: String = "",
    val apiKey: String = "",
    val voice: String = "",
    val speed: Float = 1.0f,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("base_url", baseUrl.trim())
        put("model", model.trim())
        put("api_key", apiKey.trim())
        if (voice.isNotBlank()) put("voice", voice.trim())
        if (speed != 1.0f) put("speed", speed.toDouble())
    }

    companion object {
        fun fromJson(raw: JSONObject?): ServiceProfile {
            if (raw == null) return ServiceProfile()
            return ServiceProfile(
                baseUrl = raw.optString("base_url"),
                model = raw.optString("model"),
                apiKey = raw.optString("api_key"),
                voice = raw.optString("voice"),
                speed = raw.optDouble("speed", 1.0).toFloat(),
            )
        }
    }
}

/** 客户端生成、经服务器内存转发给机器人的配置包。 */
data class RobotConfig(
    val version: Int = 1,
    val persona: String = "",
    val wakeWord: String = "小绫小绫",
    val llm: ServiceProfile = ServiceProfile(),
    val asr: ServiceProfile = ServiceProfile(),
    val tts: ServiceProfile = ServiceProfile(),
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("version", version.coerceAtLeast(1))
        put("persona", persona)
        put("wake_word", wakeWord.trim())
        put("llm", llm.toJson())
        put("asr", asr.toJson())
        put("tts", tts.toJson())
    }

    /** 返回第一条校验错误，null 表示可以推送。 */
    fun validate(): String? {
        if (llm.baseUrl.isBlank() || llm.apiKey.isBlank()) return "LLM 的 base_url 和 api_key 必填"
        if (asr.baseUrl.isBlank() || asr.apiKey.isBlank()) return "ASR 的 base_url 和 api_key 必填"
        if (tts.baseUrl.isBlank() || tts.apiKey.isBlank()) return "TTS 的 base_url 和 api_key 必填"
        if (persona.length > 4000) return "人格描述最多 4000 字"
        if (wakeWord.trim().isEmpty()) return "唤醒词不能为空"
        if (wakeWord.trim().length > 24) return "唤醒词最多 24 个字符"
        if (version !in 1..1_000_000) return "配置版本号超出范围"
        return null
    }

    companion object {
        fun fromJson(raw: String?): RobotConfig {
            if (raw.isNullOrBlank()) return RobotConfig()
            return runCatching {
                val json = JSONObject(raw)
                RobotConfig(
                    version = json.optInt("version", 1).coerceAtLeast(1),
                    persona = json.optString("persona"),
                    wakeWord = json.optString("wake_word", "小绫小绫"),
                    llm = ServiceProfile.fromJson(json.optJSONObject("llm")),
                    asr = ServiceProfile.fromJson(json.optJSONObject("asr")),
                    tts = ServiceProfile.fromJson(json.optJSONObject("tts")),
                )
            }.getOrDefault(RobotConfig())
        }
    }
}

/**
 * 默认服务商地址与模型。开源版本不预置 API Key，请在应用设置中填写自己的凭据。
 * 配置只在本机加密保存，推送时经服务器内存转发给机器人，不落库。
 */
object DefaultCredentials {
    const val DEEPSEEK_API_KEY = ""
    const val SILICONFLOW_API_KEY = ""
    const val DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
    const val SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
}

/** 默认配置：只在没保存过配置、或用户点「套用默认配置」时使用。 */
fun defaultRobotConfig(persona: String = ""): RobotConfig = RobotConfig(
    persona = persona,
    llm = ServiceProfile(
        baseUrl = DefaultCredentials.DEEPSEEK_BASE_URL,
        model = "deepseek-chat",
        apiKey = DefaultCredentials.DEEPSEEK_API_KEY,
    ),
    asr = ServiceProfile(
        baseUrl = DefaultCredentials.SILICONFLOW_BASE_URL,
        model = "FunAudioLLM/SenseVoiceSmall",
        apiKey = DefaultCredentials.SILICONFLOW_API_KEY,
    ),
    tts = ServiceProfile(
        baseUrl = DefaultCredentials.SILICONFLOW_BASE_URL,
        model = "FunAudioLLM/CosyVoice2-0.5B",
        apiKey = DefaultCredentials.SILICONFLOW_API_KEY,
        voice = "anna",
    ),
)

/**
 * 配置包只保存在本机（Keystore 加密），服务器只做内存转发。
 * 每次保存都会把版本号 +1，方便机器人判断是否需要更新。
 */
class ConfigStore(context: Context) {

    private val prefs = SecurePrefs(context, "robot_config")

    /** 首次使用（本机还没有配置包）直接给默认配置，省得从零填三套服务商。 */
    fun load(): RobotConfig {
        val raw = prefs.getString(KEY_CONFIG)
        return if (raw.isNullOrBlank()) defaultRobotConfig() else RobotConfig.fromJson(raw)
    }

    fun save(config: RobotConfig): RobotConfig {
        val next = config.copy(
            version = (config.version + 1).coerceAtLeast(1),
            wakeWord = config.wakeWord.trim(),
        )
        prefs.putString(KEY_CONFIG, next.toJson().toString())
        return next
    }

    private companion object {
        const val KEY_CONFIG = "package"
    }
}
