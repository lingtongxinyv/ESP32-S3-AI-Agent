package asia.lingtong.agent.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import asia.lingtong.agent.AppContainer
import asia.lingtong.agent.data.ChatMessageEntity
import asia.lingtong.agent.data.ChatTurn
import asia.lingtong.agent.data.DeviceInfo
import asia.lingtong.agent.data.FirmwareRelease
import asia.lingtong.agent.data.OTAEventInfo
import asia.lingtong.agent.ui.theme.BaLine
import asia.lingtong.agent.ui.theme.BaMint
import asia.lingtong.agent.ui.theme.BaNavy
import asia.lingtong.agent.ui.theme.BaNavySoft
import asia.lingtong.agent.ui.theme.BaSky
import asia.lingtong.agent.ui.theme.BaSkyDeep
import asia.lingtong.agent.ui.theme.BaSkyLight
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun RobotScreen(container: AppContainer) {
    val scope = rememberCoroutineScope()
    var devices by remember { mutableStateOf<List<DeviceInfo>>(emptyList()) }
    var selectedId by rememberSaveable { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var pulled by remember { mutableStateOf<List<ChatTurn>>(emptyList()) }
    var showPulled by remember { mutableStateOf(false) }
    var showAddDevice by remember { mutableStateOf(false) }
    var networkDevice by remember { mutableStateOf<DeviceInfo?>(null) }
    var showPanel by remember { mutableStateOf(false) }
    var firmwareReleases by remember(selectedId) { mutableStateOf<List<FirmwareRelease>>(emptyList()) }
    var otaEvents by remember(selectedId) { mutableStateOf<List<OTAEventInfo>>(emptyList()) }
    var otaChecking by remember(selectedId) { mutableStateOf(false) }
    var otaSubmitting by remember(selectedId) { mutableStateOf(false) }
    var otaMessage by remember(selectedId) { mutableStateOf("") }
    var showOtaConfirm by remember(selectedId) { mutableStateOf(false) }

    val selected = devices.firstOrNull { it.id == selectedId }
    val latestFirmware = selected?.let { latestFirmwareFor(firmwareReleases, it) }
    val latestOta = otaEvents.maxByOrNull { it.id }
    val otaActive = latestOta?.let { isOtaActive(it, selected) } == true

    LaunchedEffect(Unit) {
        busy = true
        runCatching { container.api.fetchDevices() }
            .onSuccess { list ->
                devices = list
                if (selectedId == null) selectedId = list.firstOrNull()?.id
            }
            .onFailure { status = "加载失败：${it.message}" }
        busy = false
    }

    LaunchedEffect(selectedId, otaActive) {
        val deviceId = selectedId ?: return@LaunchedEffect
        if (!otaActive) return@LaunchedEffect
        while (true) {
            delay(3_000)
            runCatching { container.api.fetchDevices() }
                .onSuccess { devices = it }
                .onFailure { otaMessage = "刷新设备信息失败：${it.message}" }
            runCatching { container.api.fetchOtaEvents(deviceId) }
                .onSuccess { otaEvents = it }
                .onFailure { otaMessage = "刷新 OTA 状态失败：${it.message}" }
        }
    }

    fun reload(message: String = "") {
        scope.launch {
            busy = true
            status = message
            runCatching { container.api.fetchDevices() }
                .onSuccess {
                    devices = it
                    if (selectedId == null) selectedId = it.firstOrNull()?.id
                }
                .onFailure { status = "加载失败：${it.message}" }
            busy = false
        }
    }

    fun checkFirmware() {
        val device = selected ?: return
        if (otaChecking || otaSubmitting) return
        scope.launch {
            otaChecking = true
            otaMessage = "检查更新中…"
            runCatching {
                val releases = container.api.fetchFirmware()
                val events = container.api.fetchOtaEvents(device.id)
                val refreshedDevices = container.api.fetchDevices()
                Triple(releases, events, refreshedDevices)
            }.onSuccess { (releases, events, refreshedDevices) ->
                firmwareReleases = releases
                otaEvents = events
                devices = refreshedDevices
                val current = refreshedDevices.firstOrNull { it.id == device.id } ?: device
                val latest = latestFirmwareFor(releases, current)
                otaMessage = when {
                    latest == null -> "暂无匹配硬件和协议的已发布固件"
                    latest.version == current.fwVersion -> "已是最新（${latest.version}）"
                    else -> "发现新版本 ${latest.version}"
                }
            }.onFailure { otaMessage = "检查更新失败：${it.message}" }
            otaChecking = false
        }
    }

    fun startOta() {
        val device = selected ?: return
        val release = latestFirmware ?: return
        if (device.fwVersion == release.version || otaActive || otaChecking || otaSubmitting) return
        scope.launch {
            otaSubmitting = true
            otaMessage = "提交更新请求中…"
            runCatching { container.api.requestOta(device.id, release.id) }
                .onSuccess { event ->
                    val targetEvent = event.copy(fwVersion = event.fwVersion.ifBlank { release.version })
                    otaEvents = otaEvents.filterNot { it.id == event.id } + targetEvent
                    otaMessage = if (device.online) "已通知检查" else "已排队，等待上线或周期检查"
                }
                .onFailure { otaMessage = "提交更新失败：${it.message}" }
            otaSubmitting = false
        }
    }

    fun pushConfig() {
        val device = selected ?: return
        val config = container.config.load()
        val invalid = config.validate()
        if (invalid != null) {
            status = invalid
            return
        }
        scope.launch {
            busy = true
            status = "推送配置中…"
            runCatching { container.api.pushConfig(device.id, config) }
                .onSuccess { result ->
                    status = if (result.delivered) {
                        "配置 v${result.version} 已下发，机器人已确认"
                    } else {
                        "配置 v${result.version} 未确认：${result.error}"
                    }
                }
                .onFailure { status = "推送失败：${it.message}" }
            busy = false
        }
    }

    fun checkStatus() {
        val device = selected ?: return
        scope.launch {
            busy = true
            runCatching { container.api.configStatus(device.id) }
                .onSuccess { result ->
                    status = if (result.version == null) {
                        "服务器内存中没有该设备的配置"
                    } else {
                        "内存配置 v${result.version}｜在线 ${if (result.online) "是" else "否"}｜已确认 ${if (result.delivered) "是" else "否"}${if (result.error.isBlank()) "" else "｜${result.error}"}"
                    }
                }
                .onFailure { status = "查询失败：${it.message}" }
            busy = false
        }
    }

    fun pullHistory() {
        val device = selected ?: return
        scope.launch {
            busy = true
            status = "拉取设备历史…"
            runCatching { container.api.pullHistory(device.id) }
                .onSuccess {
                    pulled = it
                    showPulled = true
                    status = "设备返回 ${it.size} 条历史"
                }
                .onFailure { status = "拉取失败：${it.message}" }
            busy = false
        }
    }

    fun archivePulled() {
        val device = selected ?: return
        val turns = pulled
        if (turns.isEmpty()) return
        scope.launch {
            busy = true
            container.db.chatMessages().insertAll(
                turns.map {
                    ChatMessageEntity(role = it.role, content = it.content, deviceId = device.deviceId)
                }
            )
            status = "已归档 ${turns.size} 条到本机"
            runCatching { container.api.ackHistory(device.id, turns.size) }
                .onSuccess { status = "已归档 ${turns.size} 条，并通知设备清理" }
                .onFailure { status = "已归档 ${turns.size} 条，但通知清理失败：${it.message}" }
            pulled = emptyList()
            showPulled = false
            busy = false
        }
    }

    if (showAddDevice) {
        AddDeviceScreen(
            container = container,
            onClose = { showAddDevice = false },
            onProvisioned = {
                showAddDevice = false
                reload()
            },
        )
        return
    }

    networkDevice?.let { device ->
        AddDeviceScreen(
            container = container,
            existingDevice = device,
            onClose = { networkDevice = null },
            onProvisioned = {
                networkDevice = null
                reload("网络设置已更新，机器人正在使用新网络重新连接")
            },
        )
        return
    }

    BaScreen {
        Column(modifier = Modifier.fillMaxSize()) {
            BaTopBar(
                title = "机器人",
                subtitle = "DEVICE LINK",
            ) {
                BaOutlineButton(
                    onClick = { reload() },
                    enabled = !busy,
                    modifier = Modifier
                        .height(34.dp)
                        .padding(start = 6.dp),
                ) {
                    Text("刷新")
                }
                BaButton(
                    onClick = { showAddDevice = true },
                    enabled = !busy,
                    modifier = Modifier
                        .height(34.dp)
                        .padding(start = 6.dp),
                ) {
                    Text("＋ 添加设备")
                }
            }

            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(horizontal = 12.dp)
                    .padding(top = 10.dp, bottom = 10.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                if (devices.isEmpty()) {
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxWidth(),
                        contentAlignment = Alignment.Center,
                    ) {
                        BaEmptyState(
                            title = "还没有绑定设备",
                            hint = "点右上角「添加设备」，用蓝牙给机器人配网。",
                        )
                    }
                } else {
                    LazyColumn(
                        modifier = Modifier.weight(1f),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        items(devices) { device ->
                            DeviceCard(
                                device = device,
                                selected = device.id == selectedId,
                                onClick = { selectedId = device.id },
                            )
                        }
                    }
                }

                SectionCard(title = "固件更新") {
                    Hint(
                        if (selected == null) "先在上面选中一台设备"
                        else "当前 fw：${selected.fwVersion.ifBlank { "未知" }}｜${if (selected.online) "在线" else "离线"}"
                    )
                    Hint(
                        when {
                            selected == null -> "最新已发布：选择设备后检查"
                            latestFirmware == null && firmwareReleases.isEmpty() -> "最新已发布：点击检查更新"
                            latestFirmware == null -> "最新已发布：暂无匹配固件"
                            latestFirmware.version == selected.fwVersion -> "最新已发布：${latestFirmware.version}｜已是最新"
                            else -> "最新已发布：${latestFirmware.version}"
                        }
                    )
                    if (latestFirmware != null) {
                        Hint("更新说明：${latestFirmware.notes.ifBlank { "-" }}")
                    }
                    Hint("最近 OTA：${otaStage(latestOta, selected)}")
                    if (otaMessage.isNotBlank()) Hint(otaMessage)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        BaOutlineButton(
                            onClick = { checkFirmware() },
                            enabled = selected != null && !otaChecking && !otaSubmitting,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text(if (otaChecking) "检查中…" else "检查更新")
                        }
                        BaButton(
                            onClick = { showOtaConfirm = true },
                            enabled = selected != null && latestFirmware != null &&
                                selected.fwVersion != latestFirmware.version &&
                                !otaActive && !otaChecking && !otaSubmitting,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text(if (otaSubmitting) "提交中…" else "立即更新")
                        }
                    }
                }

                SectionCard(title = "配置与历史") {
                    Hint(
                        if (selected == null) "先在上面选中一台设备"
                        else "当前：${selected.title}｜${if (selected.online) "在线" else "离线"}｜fw ${selected.fwVersion.ifBlank { "未知" }}"
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        BaButton(
                            onClick = { pushConfig() },
                            enabled = selected != null && !busy,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("推送配置")
                        }
                        BaOutlineButton(
                            onClick = { checkStatus() },
                            enabled = selected != null && !busy,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("配置状态")
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        BaOutlineButton(
                            onClick = { pullHistory() },
                            enabled = selected != null && !busy,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("拉取历史")
                        }
                        BaOutlineButton(
                            onClick = { showPanel = true },
                            enabled = selected != null,
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("屏幕与声音")
                        }
                    }
                    BaOutlineButton(
                        onClick = { networkDevice = selected },
                        enabled = selected != null && !busy,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("网络设置（修改 Wi-Fi）")
                    }
                    if (status.isNotBlank()) Hint(status)
                }
            }
        }
    }

    if (showOtaConfirm && selected != null && latestFirmware != null) {
        AlertDialog(
            onDismissRequest = { showOtaConfirm = false },
            containerColor = Color.White,
            shape = rememberCutShape(16.dp),
            title = {
                Text(
                    "确认固件更新",
                    style = MaterialTheme.typography.titleMedium,
                    color = BaNavy,
                )
            },
            text = {
                Text(
                    "将 ${selected.title} 更新到 ${latestFirmware.version}。更新期间请保持设备供电和网络连接，不要断电。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = BaNavySoft,
                )
            },
            confirmButton = {
                BaButton(
                    onClick = {
                        showOtaConfirm = false
                        startOta()
                    },
                    modifier = Modifier.height(38.dp),
                ) {
                    Text("确认更新")
                }
            },
            dismissButton = {
                BaTextButton(onClick = { showOtaConfirm = false }) { Text("取消") }
            },
        )
    }

    if (showPanel && selected != null) {
        PanelDialog(
            container = container,
            device = selected,
            onClose = { showPanel = false },
        )
    }

    if (showPulled) {
        AlertDialog(
            onDismissRequest = { showPulled = false },
            containerColor = Color.White,
            shape = rememberCutShape(16.dp),
            title = {
                Text(
                    "设备历史（${pulled.size} 条）",
                    style = MaterialTheme.typography.titleMedium,
                    color = BaNavy,
                )
            },
            text = {
                LazyColumn(modifier = Modifier.heightIn(max = 360.dp)) {
                    items(pulled) { turn ->
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 4.dp),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Box(
                                modifier = Modifier
                                    .padding(top = 6.dp)
                                    .size(5.dp)
                                    .background(BaSky, CircleShape),
                            )
                            Text(
                                text = "${if (turn.role == "user") "我" else "机器人"}：${turn.content}",
                                style = MaterialTheme.typography.bodySmall,
                                color = BaNavySoft,
                            )
                        }
                    }
                }
            },
            confirmButton = {
                BaTextButton(onClick = { archivePulled() }, enabled = pulled.isNotEmpty()) {
                    Text("归档到本机并清理")
                }
            },
            dismissButton = {
                BaTextButton(onClick = { showPulled = false }) { Text("关闭") }
            },
        )
    }
}

private fun latestFirmwareFor(
    releases: List<FirmwareRelease>,
    device: DeviceInfo,
): FirmwareRelease? = releases
    .filter {
        it.publishedAt != null && it.hardware == device.hardware && it.protocol == device.protocol
    }
    .maxWithOrNull(compareBy<FirmwareRelease> { it.publishedAt ?: "" }.thenBy { it.id })

private fun isOtaActive(event: OTAEventInfo, device: DeviceInfo?): Boolean = when {
    event.event == "result" && event.status == "success" ->
        event.fwVersion.isNotBlank() && device?.fwVersion != event.fwVersion
    event.event == "result" -> false
    event.event == "requested" && event.status == "success" ->
        event.fwVersion.isNotBlank() && device?.fwVersion != event.fwVersion
    event.event == "requested" && event.status in setOf("pending", "downloading") -> true
    event.event == "download" || event.status == "started" -> true
    else -> false
}

private fun otaStage(event: OTAEventInfo?, device: DeviceInfo?): String {
    if (event == null) return "暂无记录"
    val succeeded = event.event in setOf("requested", "result") && event.status == "success"
    if (succeeded && event.fwVersion.isNotBlank() && device?.fwVersion == event.fwVersion) {
        return "已运行新版本 ${event.fwVersion}"
    }
    return when {
        event.event == "result" && event.status == "failed" ->
            "更新失败：${event.detail.ifBlank { "未提供详情" }}"
        event.event == "result" && event.status == "success" -> "下载校验完成并等待重启"
        event.event == "requested" && event.status == "success" -> "下载校验完成并等待重启"
        event.event == "requested" && event.status == "failed" ->
            "更新失败：${event.detail.ifBlank { "未提供详情" }}"
        event.event == "download" || event.status == "started" ||
            (event.event == "requested" && event.status == "downloading") -> "正在下载"
        event.event == "requested" && event.status == "pending" -> "等待检查"
        else -> "${event.event} / ${event.status}"
    }
}

@Composable
private fun DeviceCard(device: DeviceInfo, selected: Boolean, onClick: () -> Unit) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        shape = rememberCutShape(12.dp),
        color = if (selected) Color.White else Color(0xFFF6FBFF),
        border = BorderStroke(if (selected) 1.6.dp else 1.dp, if (selected) BaSkyDeep else BaLine),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 11.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .background(if (device.online) BaMint else BaNavySoft.copy(alpha = 0.35f), CircleShape),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = device.title,
                    style = MaterialTheme.typography.titleSmall,
                    color = BaNavy,
                    modifier = Modifier.weight(1f),
                )
                if (selected) {
                    Text(
                        text = "SELECTED",
                        style = MaterialTheme.typography.labelSmall,
                        color = BaSkyDeep,
                        letterSpacing = 1.4.sp,
                        modifier = Modifier
                            .clip(RoundedCornerShape(50))
                            .background(BaSkyLight)
                            .padding(horizontal = 8.dp, vertical = 3.dp),
                    )
                }
            }
            Text(
                text = "${device.deviceId}｜${if (device.online) "在线" else "离线"}｜fw ${device.fwVersion.ifBlank { "未知" }}",
                style = MaterialTheme.typography.bodySmall,
                color = BaNavySoft,
            )
            Text(
                text = "最近在线：${device.lastSeenAt.ifBlank { "-" }}",
                style = MaterialTheme.typography.bodySmall,
                color = BaNavySoft.copy(alpha = 0.75f),
            )
        }
    }
}

private val EMOTIONS = listOf(
    "neutral" to "平静",
    "happy" to "开心",
    "sad" to "难过",
    "angry" to "生气",
    "surprised" to "惊讶",
    "thinking" to "思考",
    "sleepy" to "困倦",
    "love" to "喜欢",
)

/** 屏幕表情/文字与喇叭音量；表情字符串由固件按屏幕型号渲染。 */
@Composable
private fun PanelDialog(container: AppContainer, device: DeviceInfo, onClose: () -> Unit) {
    val scope = rememberCoroutineScope()
    var volume by remember { mutableStateOf(100f) }
    var emotion by remember { mutableStateOf("neutral") }
    var text by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }

    LaunchedEffect(device.id) {
        runCatching { container.api.panelState(device.id) }
            .onSuccess {
                volume = it.volume.toFloat()
                emotion = it.emotion
                text = it.text
                if (!it.online) status = "设备当前离线，指令需要机器人上线后才能生效"
            }
            .onFailure { status = "读取状态失败：${it.message}" }
    }

    fun send(clear: Boolean) {
        scope.launch {
            busy = true
            status = if (clear) "恢复默认中…" else "下发中…"
            runCatching {
                container.api.sendPanel(
                    deviceId = device.id,
                    volume = volume.toInt(),
                    emotion = if (clear) null else emotion,
                    text = if (clear) "" else text.trim(),
                    clear = clear,
                )
            }.onSuccess {
                status = if (it.delivered) {
                    "已下发：${if (clear) "恢复默认界面" else "表情 $emotion，音量 ${it.volume}%"}，机器人已确认"
                } else {
                    "未确认：${it.error}"
                }
            }.onFailure { status = "下发失败：${it.message}" }
            busy = false
        }
    }

    AlertDialog(
        onDismissRequest = onClose,
        containerColor = Color.White,
        shape = rememberCutShape(16.dp),
        title = {
            Text(
                "屏幕与声音",
                style = MaterialTheme.typography.titleMedium,
                color = BaNavy,
            )
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Hint("设备：${device.id}")
                Hint("表情和文字下发给机器人后锁定屏幕，点「恢复默认」交还状态界面。文字仅支持英文和数字。")
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    EMOTIONS.forEach { (key, label) ->
                        BaChip(
                            selected = emotion == key,
                            onClick = { emotion = key },
                            label = { Text(label) },
                        )
                    }
                }
                BaTextField(
                    value = text,
                    onValueChange = { if (it.length <= 21) text = it },
                    label = { Text("屏幕文字（可选，最多 21 字符）") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "音量",
                        style = MaterialTheme.typography.bodyMedium,
                        color = BaNavy,
                    )
                    BaSlider(
                        value = volume,
                        onValueChange = { volume = it },
                        valueRange = 0f..100f,
                        steps = 9,
                        modifier = Modifier
                            .weight(1f)
                            .padding(horizontal = 8.dp),
                    )
                    Text(
                        "${volume.toInt()}%",
                        style = MaterialTheme.typography.labelLarge,
                        color = BaSkyDeep,
                    )
                }
                if (status.isNotBlank()) Hint(status)
            }
        },
        confirmButton = {
            Row(
                horizontalArrangement = Arrangement.spacedBy(2.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                BaTextButton(onClick = { send(true) }, enabled = !busy) { Text("恢复默认") }
                BaTextButton(onClick = onClose) { Text("关闭") }
                BaButton(
                    onClick = { send(false) },
                    enabled = !busy,
                    modifier = Modifier.height(38.dp),
                ) {
                    Text(if (busy) "下发中…" else "下发")
                }
            }
        },
        dismissButton = null,
    )
}
