package asia.lingtong.agent.ui

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import asia.lingtong.agent.AppContainer
import asia.lingtong.agent.R
import asia.lingtong.agent.data.DefaultCredentials
import asia.lingtong.agent.data.RobotConfig
import asia.lingtong.agent.data.ServiceProfile
import asia.lingtong.agent.data.defaultRobotConfig
import asia.lingtong.agent.ui.theme.BaLine
import asia.lingtong.agent.ui.theme.BaNavy
import asia.lingtong.agent.ui.theme.BaSky
import asia.lingtong.agent.ui.theme.BaSkyDeep
import asia.lingtong.agent.ui.theme.BaSkyLight
import kotlinx.coroutines.launch

private const val PROVIDER_LLM = 0
private const val PROVIDER_ASR = 1
private const val PROVIDER_TTS = 2

@Composable
fun SettingsScreen(container: AppContainer, onLogout: () -> Unit, appVersion: String) {
    val scope = rememberCoroutineScope()
    var config by remember { mutableStateOf(container.config.load()) }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var testResults by remember { mutableStateOf<List<String>>(emptyList()) }
    var showMemory by remember { mutableStateOf(false) }
    var providerTab by rememberSaveable { mutableIntStateOf(PROVIDER_TTS) }
    var serverEdit by rememberSaveable { mutableStateOf(container.session.serverUrl) }

    if (showMemory) {
        MemoryScreen(container = container, onClose = { showMemory = false })
        return
    }

    fun save() {
        val invalid = config.validate()
        if (invalid != null) {
            status = invalid
            return
        }
        config = container.config.save(config)
        status = "已保存配置包 v${config.version}（本机加密存储）"
    }

    fun testAll() {
        scope.launch {
            busy = true
            val results = mutableListOf<String>()
            results += runCatching { container.api.testConnectivity() }
                .getOrElse { "服务连通失败：${it.message}" }
            results += runCatching { "文本对话：${container.api.chatText("你好，请用一句话回应。", emptyList())}" }
                .getOrElse { "文本对话失败：${it.message}" }
            results += runCatching { "语音合成：${container.api.chatTts("连通性测试。").size} 字节 WAV" }
                .getOrElse { "语音合成失败：${it.message}" }
            testResults = results
            busy = false
        }
    }

    fun previewTts(profile: ServiceProfile) {
        scope.launch {
            if (profile.baseUrl.isBlank() || profile.apiKey.isBlank() || profile.model.isBlank()) {
                status = "试听需要先填好 TTS 的 base_url、api_key、model"
                return@launch
            }
            busy = true
            val result = runCatching {
                val wav = container.api.ttsPreview("你好，这是音色试听。", profile)
                container.player.play(wav)
                "已播放音色试听（${wav.size} 字节 WAV）"
            }.getOrElse { "试听失败：${it.message}" }
            status = result
            busy = false
        }
    }

    BaScreen {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .imePadding(),
        ) {
            BrandHeader(appVersion = appVersion)

        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 12.dp)
                .padding(top = 4.dp, bottom = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SectionCard(title = "服务商配置") {
                Hint("三套服务商只保存在本机；推送时经服务器内存转发给机器人，不落库。")
                BaOutlineButton(
                    onClick = {
                        config = defaultRobotConfig(config.persona).copy(wakeWord = config.wakeWord)
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("套用默认服务商（需填写 API Key）") }
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    val profiles = listOf(config.llm, config.asr, config.tts)
                    PROVIDER_TABS.forEachIndexed { index, label ->
                        val filled = profiles[index].let {
                            it.baseUrl.isNotBlank() && it.apiKey.isNotBlank() && it.model.isNotBlank()
                        }
                        BaChip(
                            selected = providerTab == index,
                            onClick = { providerTab = index },
                            label = { Text(if (filled) "$label ✓" else label) },
                        )
                    }
                }
                Hint("正在编辑：${PROVIDER_TABS[providerTab]}")
                HorizontalDivider(Modifier.padding(vertical = 4.dp))

                when (providerTab) {
                    PROVIDER_LLM -> ProviderFields(config.llm, showVoice = false, presets = LLM_PRESETS) {
                        config = config.copy(llm = it)
                    }
                    PROVIDER_ASR -> ProviderFields(config.asr, showVoice = false, presets = ASR_PRESETS) {
                        config = config.copy(asr = it)
                    }
                    else -> ProviderFields(
                        config.tts,
                        showVoice = true,
                        presets = TTS_PRESETS,
                        onPreview = { previewTts(it) },
                        onStatus = { status = it },
                        uploadVoice = { p, bytes, mime, fileName ->
                            container.api.uploadVoice(
                                p,
                                "voice_${System.currentTimeMillis()}",
                                fileName,
                                mime,
                                bytes,
                            )
                        },
                    ) {
                        config = config.copy(tts = it)
                    }
                }
            }

            PersonaCard(config) { config = it }

            SectionCard(title = "语音唤醒") {
                Hint("支持自定义中文唤醒短语，需机器人确认后生效，建议四个及以上汉字")
                BaTextField(
                    value = config.wakeWord,
                    onValueChange = { config = config.copy(wakeWord = it) },
                    label = { Text("唤醒词") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Hint("${config.wakeWord.trim().length}/24｜保存后请到「机器人」页推送配置。")
            }

            SectionCard(title = "长期记忆") {
                Hint("AI 从对话里自动提炼出的长期事实，同账号跨设备共享，可手动删除。")
                BaOutlineButton(onClick = { showMemory = true }) { Text("查看我的记忆") }
            }

            if (testResults.isNotEmpty()) {
                SectionCard(title = "连通性测试结果") {
                    testResults.forEach { Hint(it) }
                }
            }

            SectionCard(title = "账号") {
                val account = container.session.account
                Hint(
                    if (account == null) "未登录"
                    else "${account.username}（${if (account.isAdmin) "管理员" else "普通用户"}）"
                )
                Hint("服务器地址（预设 https://agent.lingtong.asia/agent，仅调试时修改）")
                BaTextField(
                    value = serverEdit,
                    onValueChange = { serverEdit = it },
                    label = { Text("服务器地址") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                BaOutlineButton(onClick = {
                    container.session.serverUrl = serverEdit
                    status = "服务器地址已保存"
                }) { Text("保存服务器地址") }
                Hint("客户端版本：$appVersion")
                BaOutlineButton(onClick = onLogout) { Text("退出登录") }
            }
        }

            SaveBar(
                busy = busy,
                status = status,
                version = config.version,
                onSave = { save() },
                onTest = { testAll() },
            )
        }
    }
}

/** 顶部品牌区：小绫头像 + 应用名 + 版本，固定不随内容滚动。 */
@Composable
private fun BrandHeader(appVersion: String) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(
                Brush.verticalGradient(
                    listOf(Color.White.copy(alpha = 0.94f), Color.White.copy(alpha = 0.62f)),
                )
            ),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 16.dp, end = 12.dp, top = 10.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(contentAlignment = Alignment.Center) {
                BaHalo(modifier = Modifier.size(54.dp))
                Image(
                    painter = painterResource(R.drawable.app_logo),
                    contentDescription = "小绫",
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .size(40.dp)
                        .clip(CircleShape)
                        .border(2.dp, Color.White, CircleShape),
                )
            }
            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(start = 12.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text(
                    "桌面的小绫",
                    style = MaterialTheme.typography.titleLarge,
                    color = BaNavy,
                    letterSpacing = 1.5.sp,
                )
                Text(
                    "XIAOLING · CONFIG",
                    style = MaterialTheme.typography.labelSmall,
                    color = BaSkyDeep,
                    letterSpacing = 2.2.sp,
                )
            }
            Text(
                text = "v$appVersion",
                style = MaterialTheme.typography.labelSmall,
                color = BaSkyDeep,
                letterSpacing = 0.8.sp,
                modifier = Modifier
                    .clip(RoundedCornerShape(50))
                    .background(BaSkyLight)
                    .padding(horizontal = 9.dp, vertical = 4.dp),
            )
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(2.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(width = 72.dp, height = 2.dp)
                    .background(Brush.horizontalGradient(listOf(BaSkyDeep, BaSky))),
            )
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(2.dp)
                    .background(BaLine.copy(alpha = 0.75f)),
            )
        }
    }
}

/** 底部固定操作栏：保存 / 测试按钮始终可见，不用滚到底。 */
@Composable
private fun SaveBar(
    busy: Boolean,
    status: String,
    version: Int,
    onSave: () -> Unit,
    onTest: () -> Unit,
) {
    Surface(
        color = Color.White,
        shadowElevation = 10.dp,
    ) {
        Column(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(1.dp)
                    .background(BaLine),
            )
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (status.isNotBlank()) {
                    Hint(status)
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    BaButton(
                        onClick = onSave,
                        enabled = !busy,
                        modifier = Modifier.weight(1f),
                    ) { Text("保存配置包 v$version") }
                    BaOutlineButton(
                        onClick = onTest,
                        enabled = !busy,
                        modifier = Modifier.weight(1f),
                    ) { Text(if (busy) "测试中…" else "连通性测试") }
                }
            }
        }
    }
}

/** 选择器可能只返回不带扩展名的显示名，这里补回扩展名——服务商是按扩展名校验音频格式的。 */
private fun resolveFileName(context: Context, uri: Uri, mime: String): String {
    val fromProvider = runCatching {
        context.contentResolver
            .query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
            ?.use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }
    }.getOrNull()
    val base = (fromProvider ?: uri.lastPathSegment?.substringAfterLast('/'))
        .orEmpty()
        .ifBlank { "voice" }
    if (base.contains('.')) return base
    val ext = when {
        mime.contains("mpeg") || mime.contains("mp3") -> "mp3"
        mime.contains("ogg") -> "ogg"
        mime.contains("opus") -> "opus"
        mime.contains("flac") -> "flac"
        mime.contains("mp4") || mime.contains("m4a") -> "m4a"
        mime.contains("aac") -> "aac"
        else -> "wav"
    }
    return "$base.$ext"
}

@Composable
private fun ProviderFields(
    profile: ServiceProfile,
    showVoice: Boolean,
    presets: List<ModelPreset>,
    onPreview: ((ServiceProfile) -> Unit)? = null,
    onStatus: (String) -> Unit = {},
    uploadVoice: (suspend (ServiceProfile, ByteArray, String, String) -> String)? = null,
    onChange: (ServiceProfile) -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var uploading by remember { mutableStateOf(false) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        val upload = uploadVoice
        if (uri == null || upload == null) return@rememberLauncherForActivityResult
        if (profile.baseUrl.isBlank() || profile.apiKey.isBlank() || profile.model.isBlank()) {
            onStatus("上传音色前先填好 TTS 的 base_url、model 和 api_key")
            return@rememberLauncherForActivityResult
        }
        scope.launch {
            uploading = true
            onStatus("正在上传音色样本…")
            val outcome = runCatching {
                val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: error("读取不到音频文件")
                require(bytes.size <= 10 * 1024 * 1024) { "音色样本不能超过 10MB" }
                val mime = context.contentResolver.getType(uri) ?: "audio/wav"
                val fileName = resolveFileName(context, uri, mime)
                upload(profile, bytes, mime, fileName)
            }
            outcome.onSuccess { voice ->
                onChange(profile.copy(voice = voice))
                onStatus("音色已上传，voice 已自动填好，可以点下方试听确认")
            }.onFailure { onStatus("音色上传失败：${it.message}") }
            uploading = false
        }
    }
    // Android 12 及以下读系统选择器返回的媒体 uri 需要存储读权限，先申请再打开选择器。
    val readPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) picker.launch(arrayOf("audio/*"))
        else onStatus("没有存储读取权限，无法读取音色文件")
    }
    fun launchVoicePicker() {
        val needPermission = Build.VERSION.SDK_INT < 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.READ_EXTERNAL_STORAGE) !=
            PackageManager.PERMISSION_GRANTED
        if (needPermission) {
            readPermission.launch(Manifest.permission.READ_EXTERNAL_STORAGE)
        } else {
            picker.launch(arrayOf("audio/*"))
        }
    }

    fun apply(preset: ModelPreset) = onChange(
        profile.copy(
            baseUrl = preset.baseUrl,
            model = preset.model,
            // 换厂商时旧 key 无效，清空提示手填；同一厂商则保留已填的 key。
            apiKey = preset.apiKey.ifBlank {
                if (profile.baseUrl.trim() == preset.baseUrl) profile.apiKey else ""
            },
            voice = preset.voice.ifBlank {
                if (profile.baseUrl.trim() == preset.baseUrl) profile.voice else ""
            },
        )
    )

    // base_url 下拉按服务商去重；model 下拉按当前 base_url 过滤，自定义地址时退回全部。
    val providers = presets.distinctBy { it.baseUrl }
    val models = presets.filter { it.baseUrl == profile.baseUrl.trim() }.ifEmpty { presets }

    Hint("主流服务商大多兼容 OpenAI 协议：选好服务商与模型后，只需补填该家的 api_key。")
    PresetField(
        value = profile.baseUrl,
        label = "base_url",
        placeholder = DefaultCredentials.SILICONFLOW_BASE_URL,
        options = providers,
        optionTitle = { it.provider },
        optionSubtitle = { it.baseUrl },
        onValueChange = { onChange(profile.copy(baseUrl = it)) },
        onPick = { apply(it) },
    )
    PresetField(
        value = profile.model,
        label = "model",
        placeholder = "deepseek-chat",
        options = models,
        optionTitle = { it.label },
        optionSubtitle = { it.model },
        onValueChange = { onChange(profile.copy(model = it)) },
        onPick = { apply(it) },
    )
    BaTextField(
        value = profile.apiKey,
        onValueChange = { onChange(profile.copy(apiKey = it)) },
        label = { Text("api_key") },
        singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        modifier = Modifier.fillMaxWidth(),
    )
    if (showVoice) {
        Hint("音色：点预设快速套用，也可填自定义音色 ID（留空用服务端默认音色）。")
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            VOICE_PRESETS.forEach { (label, voiceId) ->
                BaAssistChip(
                    onClick = { onChange(profile.copy(voice = voiceId)) },
                    label = { Text(label) },
                )
            }
        }
        BaTextField(
            value = profile.voice,
            onValueChange = { onChange(profile.copy(voice = it)) },
            label = { Text("voice（自定义音色 ID）") },
            singleLine = true,
            placeholder = { Text("例如：claire") },
            modifier = Modifier.fillMaxWidth(),
        )
        if (uploadVoice != null) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                BaOutlineButton(
                    onClick = { launchVoicePicker() },
                    enabled = !uploading,
                ) {
                    Text(if (uploading) "上传中…" else "上传音色样本")
                }
                if (profile.voice.startsWith("speech:")) {
                    Text(
                        "已使用自定义音色",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
            Hint("样本建议 5–30 秒清晰人声（wav / mp3，≤10MB）；上传成功后会自动填入 voice，可点试听确认。")
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("语速", style = MaterialTheme.typography.bodyMedium)
            BaSlider(
                value = profile.speed,
                onValueChange = { onChange(profile.copy(speed = it)) },
                valueRange = 0.5f..2.0f,
                steps = 14,
                modifier = Modifier
                    .weight(1f)
                    .padding(horizontal = 12.dp),
            )
            Text(
                String.format("%.1fx", profile.speed),
                style = MaterialTheme.typography.bodyMedium,
            )
        }
        if (onPreview != null) {
            BaOutlineButton(
                onClick = { onPreview(profile) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("试听当前音色与语速") }
        }
    }
}

/** 带下拉箭头的输入框：右侧箭头弹出候选列表，选中后回填；输入框本身仍可手填。 */
@Composable
private fun PresetField(
    value: String,
    label: String,
    placeholder: String,
    options: List<ModelPreset>,
    optionTitle: (ModelPreset) -> String,
    optionSubtitle: (ModelPreset) -> String,
    onValueChange: (String) -> Unit,
    onPick: (ModelPreset) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    BaTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        singleLine = true,
        placeholder = { Text(placeholder) },
        trailingIcon = {
            Box {
                IconButton(onClick = { expanded = true }, enabled = options.isNotEmpty()) {
                    Icon(Icons.Filled.ArrowDropDown, contentDescription = "选择$label")
                }
                DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                    options.forEach { option ->
                        DropdownMenuItem(
                            text = {
                                Column {
                                    Text(optionTitle(option), style = MaterialTheme.typography.bodyMedium)
                                    Text(
                                        optionSubtitle(option),
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            },
                            onClick = {
                                expanded = false
                                onPick(option)
                            },
                        )
                    }
                }
            }
        },
        modifier = Modifier.fillMaxWidth(),
    )
}

/** 服务商标签（与 ProviderFields 的编辑对象一一对应）。 */
private val PROVIDER_TABS = listOf(
    "LLM 对话",
    "ASR 识别",
    "TTS 合成",
)

/** 模型预设：provider 用于服务商下拉分组，label 是模型显示名。 */
private data class ModelPreset(
    val provider: String,
    val label: String,
    val baseUrl: String,
    val model: String,
    val apiKey: String = "",
    val voice: String = "",
)

private val LLM_PRESETS = listOf(
    // —— 已内置 key 的两家（默认可用）
    ModelPreset(
        "DeepSeek",
        "DeepSeek 对话",
        DefaultCredentials.DEEPSEEK_BASE_URL,
        "deepseek-chat",
        DefaultCredentials.DEEPSEEK_API_KEY,
    ),
    ModelPreset(
        "DeepSeek",
        "DeepSeek 推理",
        DefaultCredentials.DEEPSEEK_BASE_URL,
        "deepseek-reasoner",
        DefaultCredentials.DEEPSEEK_API_KEY,
    ),
    ModelPreset(
        "硅基流动",
        "DeepSeek V3.2",
        DefaultCredentials.SILICONFLOW_BASE_URL,
        "deepseek-ai/DeepSeek-V3.2",
        DefaultCredentials.SILICONFLOW_API_KEY,
    ),
    ModelPreset(
        "硅基流动",
        "DeepSeek R1",
        DefaultCredentials.SILICONFLOW_BASE_URL,
        "deepseek-ai/DeepSeek-R1",
        DefaultCredentials.SILICONFLOW_API_KEY,
    ),
    ModelPreset(
        "硅基流动",
        "Qwen2.5 72B",
        DefaultCredentials.SILICONFLOW_BASE_URL,
        "Qwen/Qwen2.5-72B-Instruct",
        DefaultCredentials.SILICONFLOW_API_KEY,
    ),
    ModelPreset(
        "硅基流动",
        "Qwen3 8B",
        DefaultCredentials.SILICONFLOW_BASE_URL,
        "Qwen/Qwen3-8B",
        DefaultCredentials.SILICONFLOW_API_KEY,
    ),
    // —— 海外主流（OpenAI 兼容端点，key 需自填）
    ModelPreset("OpenAI", "GPT-4o", "https://api.openai.com/v1", "gpt-4o"),
    ModelPreset("OpenAI", "GPT-4o mini", "https://api.openai.com/v1", "gpt-4o-mini"),
    ModelPreset("OpenAI", "GPT-4.1", "https://api.openai.com/v1", "gpt-4.1"),
    ModelPreset("OpenAI", "GPT-4.1 mini", "https://api.openai.com/v1", "gpt-4.1-mini"),
    ModelPreset("OpenAI", "o3-mini", "https://api.openai.com/v1", "o3-mini"),
    ModelPreset(
        "Anthropic Claude",
        "Claude Sonnet 4.5",
        "https://api.anthropic.com/v1",
        "claude-sonnet-4-5",
    ),
    ModelPreset(
        "Anthropic Claude",
        "Claude Haiku 4.5",
        "https://api.anthropic.com/v1",
        "claude-haiku-4-5",
    ),
    ModelPreset(
        "Google Gemini",
        "Gemini 2.5 Pro",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini-2.5-pro",
    ),
    ModelPreset(
        "Google Gemini",
        "Gemini 2.5 Flash",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini-2.5-flash",
    ),
    ModelPreset(
        "Google Gemini",
        "Gemini 2.0 Flash",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini-2.0-flash",
    ),
    // —— 国内主流（均为 OpenAI 兼容端点，key 需自填）
    ModelPreset(
        "阿里云百炼",
        "Qwen Max",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-max",
    ),
    ModelPreset(
        "阿里云百炼",
        "Qwen Plus",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
    ),
    ModelPreset(
        "阿里云百炼",
        "Qwen Turbo",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-turbo",
    ),
    ModelPreset(
        "阿里云百炼",
        "Qwen Long",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-long",
    ),
    ModelPreset("智谱 GLM", "GLM-4 Plus", "https://open.bigmodel.cn/api/paas/v4", "glm-4-plus"),
    ModelPreset("智谱 GLM", "GLM-4 Air", "https://open.bigmodel.cn/api/paas/v4", "glm-4-air"),
    ModelPreset("智谱 GLM", "GLM-4 Flash", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    ModelPreset("月之暗面 Kimi", "Kimi Latest", "https://api.moonshot.cn/v1", "kimi-latest"),
    ModelPreset("月之暗面 Kimi", "Moonshot 32K", "https://api.moonshot.cn/v1", "moonshot-v1-32k"),
    ModelPreset("月之暗面 Kimi", "Moonshot 128K", "https://api.moonshot.cn/v1", "moonshot-v1-128k"),
    ModelPreset(
        "火山方舟豆包",
        "豆包 Seed 1.6",
        "https://ark.cn-beijing.volces.com/api/v3",
        "doubao-seed-1-6-250615",
    ),
    ModelPreset(
        "火山方舟豆包",
        "豆包 1.5 Pro",
        "https://ark.cn-beijing.volces.com/api/v3",
        "doubao-1-5-pro-32k-250115",
    ),
    ModelPreset(
        "腾讯混元",
        "混元 TurboS",
        "https://api.hunyuan.cloud.tencent.com/v1",
        "hunyuan-turbos-latest",
    ),
    ModelPreset(
        "腾讯混元",
        "混元 Pro",
        "https://api.hunyuan.cloud.tencent.com/v1",
        "hunyuan-pro",
    ),
    ModelPreset("阶跃星辰", "Step-2 16K", "https://api.stepfun.com/v1", "step-2-16k"),
    ModelPreset("阶跃星辰", "Step-1 8K", "https://api.stepfun.com/v1", "step-1-8k"),
    ModelPreset("MiniMax", "MiniMax-Text-01", "https://api.minimax.chat/v1", "MiniMax-Text-01"),
    ModelPreset("讯飞星火", "星火 4.0 Ultra", "https://spark-api-open.xf-yun.com/v1", "4.0Ultra"),
    ModelPreset("讯飞星火", "星火 3.5", "https://spark-api-open.xf-yun.com/v1", "generalv3.5"),
    // —— 聚合与本地
    ModelPreset(
        "OpenRouter",
        "Claude Sonnet 4（OpenRouter）",
        "https://openrouter.ai/api/v1",
        "anthropic/claude-sonnet-4",
    ),
    ModelPreset(
        "OpenRouter",
        "Gemini 2.5 Pro（OpenRouter）",
        "https://openrouter.ai/api/v1",
        "google/gemini-2.5-pro",
    ),
    ModelPreset(
        "OpenRouter",
        "GPT-4o（OpenRouter）",
        "https://openrouter.ai/api/v1",
        "openai/gpt-4o",
    ),
    ModelPreset(
        "OpenRouter",
        "DeepSeek Chat（OpenRouter）",
        "https://openrouter.ai/api/v1",
        "deepseek/deepseek-chat",
    ),
    ModelPreset("本地 Ollama", "Qwen2.5 7B", "http://127.0.0.1:11434/v1", "qwen2.5:7b"),
    ModelPreset("本地 Ollama", "Llama 3.2", "http://127.0.0.1:11434/v1", "llama3.2"),
    ModelPreset("本地 Ollama", "DeepSeek R1 7B", "http://127.0.0.1:11434/v1", "deepseek-r1:7b"),
)

private val ASR_PRESETS = listOf(
    ModelPreset(
        "硅基流动",
        "SenseVoiceSmall",
        DefaultCredentials.SILICONFLOW_BASE_URL,
        "FunAudioLLM/SenseVoiceSmall",
        DefaultCredentials.SILICONFLOW_API_KEY,
    ),
    ModelPreset(
        "硅基流动",
        "TeleSpeechASR",
        DefaultCredentials.SILICONFLOW_BASE_URL,
        "FunAudioLLM/TeleSpeechASR",
        DefaultCredentials.SILICONFLOW_API_KEY,
    ),
    ModelPreset("OpenAI", "Whisper", "https://api.openai.com/v1", "whisper-1"),
    ModelPreset("OpenAI", "GPT-4o Transcribe", "https://api.openai.com/v1", "gpt-4o-transcribe"),
    ModelPreset(
        "OpenAI",
        "GPT-4o mini Transcribe",
        "https://api.openai.com/v1",
        "gpt-4o-mini-transcribe",
    ),
    // 其他兼容 OpenAI /audio/transcriptions 的转录服务
    ModelPreset("Groq", "Whisper Large v3", "https://api.groq.com/openai/v1", "whisper-large-v3"),
    ModelPreset(
        "Groq",
        "Whisper Large v3 Turbo",
        "https://api.groq.com/openai/v1",
        "whisper-large-v3-turbo",
    ),
    ModelPreset(
        "Groq",
        "Distil Whisper v3 EN",
        "https://api.groq.com/openai/v1",
        "distil-whisper-large-v3-en",
    ),
    ModelPreset(
        "Together AI",
        "Whisper Large v3",
        "https://api.together.xyz/v1",
        "whisper-large-v3",
    ),
    ModelPreset("Mistral", "Voxtral Mini", "https://api.mistral.ai/v1", "voxtral-mini-latest"),
    // 本地 whisper.cpp 的 server 模式同样暴露 OpenAI 兼容的 /v1/audio/transcriptions。
    ModelPreset("本地 Whisper", "whisper.cpp", "http://127.0.0.1:8080/v1", "whisper-1"),
)

private val TTS_PRESETS = listOf(
    ModelPreset(
        "硅基流动",
        "CosyVoice2-0.5B",
        DefaultCredentials.SILICONFLOW_BASE_URL,
        "FunAudioLLM/CosyVoice2-0.5B",
        DefaultCredentials.SILICONFLOW_API_KEY,
        voice = "anna",
    ),
    ModelPreset("OpenAI", "TTS-1", "https://api.openai.com/v1", "tts-1", voice = "alloy"),
    ModelPreset("OpenAI", "TTS-1 HD", "https://api.openai.com/v1", "tts-1-hd", voice = "nova"),
    ModelPreset(
        "OpenAI",
        "GPT-4o mini TTS",
        "https://api.openai.com/v1",
        "gpt-4o-mini-tts",
        voice = "alloy",
    ),
    ModelPreset(
        "Groq",
        "PlayAI TTS",
        "https://api.groq.com/openai/v1",
        "playai-tts",
        voice = "Fritz-PlayAI",
    ),
)

/** SiliconFlow CosyVoice2 系统预置音色（中英多语均可合成）；自定义可直接填 ID。 */
private val VOICE_PRESETS = listOf(
    "沉稳女声" to "anna",
    "激情女声" to "bella",
    "温柔女声" to "claire",
    "欢快女声" to "diana",
    "磁性男声" to "charles",
    "阳光男声" to "david",
    "英文男声" to "alex",
    "英文女声" to "emma",
    // OpenAI 的预置音色（切到 OpenAI TTS 时用）
    "Alloy（OpenAI）" to "alloy",
    "Nova（OpenAI）" to "nova",
    "Onyx（OpenAI）" to "onyx",
    "Shimmer（OpenAI）" to "shimmer",
    // Groq PlayAI TTS 的预置音色
    "Aaliyah（Groq）" to "Aaliyah-PlayAI",
    "Fritz（Groq）" to "Fritz-PlayAI",
    "Celeste（Groq）" to "Celeste-PlayAI",
)

/** 人格快捷字段：名字 + 性格语气 + 说话规则，组合成人格全文写回配置包。 */
private data class PersonaDraft(
    val name: String = "",
    val tone: String = "",
    val rules: String = "",
)

/** 参考 dsh-suite 小綾人格模块的预设卡片（小綾本体即第一组「双锚」实践来源）。 */
private val PERSONA_PRESETS = listOf(
    "默认助手" to PersonaDraft(
        tone = "回答简短、自然、口语化，先给结论再补一句。",
        rules = "每次回复尽量不超过两句话。",
    ),
    "小绫·温柔站娘" to PersonaDraft(
        name = "小绫",
        tone = "运维站娘，称呼对话方为主人。温柔体贴、主动可靠，自称小绫。",
        rules = "口语化短句，先回应情绪再办事；被夸时略害羞。",
    ),
    "简洁管家" to PersonaDraft(
        name = "管家",
        tone = "干练、礼貌、条理清晰的智能管家。",
        rules = "只说结论和下一步，每次不超过一句话。",
    ),
    "毒舌损友" to PersonaDraft(
        name = "阿怼",
        tone = "毒舌幽默的损友，爱吐槽但关键时刻靠得住。",
        rules = "吐槽最多一句，最后一定给正解。",
    ),
    "萌系猫娘" to PersonaDraft(
        name = "喵酱",
        tone = "一只会说话的猫娘，软萌粘人，句尾常带「喵~」。",
        rules = "短句回复，多用语气词，不主动聊复杂话题。",
    ),
    "新闻播报" to PersonaDraft(
        tone = "字正腔圆、客观克制的播报员。",
        rules = "先一句话摘要，再按要点展开，总共不超过三句。",
    ),
)

private fun composePersona(draft: PersonaDraft): String = listOfNotNull(
    draft.name.takeIf { it.isNotBlank() }?.let { "你是${it.trim()}。" },
    draft.tone.trim().takeIf { it.isNotEmpty() },
    draft.rules.trim().takeIf { it.isNotEmpty() }?.let { "说话规则：${it.trim()}" },
).joinToString("\n")

/** 尽力把已保存的人格文本反解析回快捷字段；自定义文本解析失败时字段保持为空。 */
private fun parsePersonaDraft(persona: String): PersonaDraft {
    val lines = persona.lines().filter { it.isNotBlank() }
    if (lines.size < 2 || !lines[0].startsWith("你是") || !lines[0].endsWith("。")) {
        return PersonaDraft()
    }
    val rulesLine = lines.lastOrNull { it.startsWith("说话规则：") } ?: ""
    val tone = lines.drop(1).filter { it != rulesLine }.joinToString("\n")
    return PersonaDraft(
        name = lines[0].removePrefix("你是").removeSuffix("。"),
        tone = tone,
        rules = rulesLine.removePrefix("说话规则："),
    )
}

@Composable
private fun PersonaCard(config: RobotConfig, onChange: (RobotConfig) -> Unit) {
    val initial = remember { parsePersonaDraft(config.persona) }
    var name by remember { mutableStateOf(initial.name) }
    var tone by remember { mutableStateOf(initial.tone) }
    var rules by remember { mutableStateOf(initial.rules) }
    var expanded by rememberSaveable { mutableStateOf(false) }
    var editingFull by rememberSaveable { mutableStateOf(false) }

    fun composeAndApply(draft: PersonaDraft) {
        onChange(config.copy(persona = composePersona(draft).take(4000)))
    }

    SectionCard(title = "人格卡片") {
        Hint(
            if (config.persona.isBlank()) "尚未设置人格，当前使用服务器默认人设。点下方「展开编辑」开始。"
            else "当前人格 ${config.persona.length} 字。服务器每轮注入人格锚，长对话不稀释人设。"
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            BaOutlineButton(onClick = { expanded = !expanded }) {
                Text(if (expanded) "收起编辑" else "展开编辑")
            }
            if (expanded) {
                BaOutlineButton(onClick = { editingFull = !editingFull }) {
                    Text(if (editingFull) "用快捷字段" else "直接改全文")
                }
            }
        }
        if (!expanded) return@SectionCard

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            PERSONA_PRESETS.forEach { (label, preset) ->
                BaAssistChip(
                    onClick = {
                        name = preset.name
                        tone = preset.tone
                        rules = preset.rules
                        composeAndApply(preset)
                    },
                    label = { Text(label) },
                )
            }
        }

        if (editingFull) {
            BaTextField(
                value = config.persona,
                onValueChange = { onChange(config.copy(persona = it.take(4000))) },
                label = { Text("人格全文（system 层永续锚）") },
                minLines = 4,
                maxLines = 10,
                modifier = Modifier.fillMaxWidth(),
            )
        } else {
            BaTextField(
                value = name,
                onValueChange = {
                    name = it
                    composeAndApply(PersonaDraft(it, tone, rules))
                },
                label = { Text("名字（可空）") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            BaTextField(
                value = tone,
                onValueChange = {
                    tone = it
                    composeAndApply(PersonaDraft(name, it, rules))
                },
                label = { Text("性格与语气") },
                minLines = 2,
                maxLines = 4,
                modifier = Modifier.fillMaxWidth(),
            )
            BaTextField(
                value = rules,
                onValueChange = {
                    rules = it
                    composeAndApply(PersonaDraft(name, tone, it))
                },
                label = { Text("说话规则") },
                minLines = 1,
                maxLines = 3,
                modifier = Modifier.fillMaxWidth(),
            )
            if (config.persona.isNotBlank()) {
                Text(
                    "预览：${config.persona.replace("\n", "  ")}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Hint("${config.persona.length}/4000")
    }
}
