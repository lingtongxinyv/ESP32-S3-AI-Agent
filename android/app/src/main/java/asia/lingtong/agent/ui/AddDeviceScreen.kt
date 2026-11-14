package asia.lingtong.agent.ui

import android.content.Context
import android.net.wifi.WifiManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import asia.lingtong.agent.AppContainer
import asia.lingtong.agent.data.BleProvisioner
import asia.lingtong.agent.data.DeviceInfo
import asia.lingtong.agent.data.FoundDevice
import asia.lingtong.agent.ui.theme.BaLine
import asia.lingtong.agent.ui.theme.BaNavy
import asia.lingtong.agent.ui.theme.BaSky
import asia.lingtong.agent.ui.theme.BaSkyDeep
import kotlinx.coroutines.launch

@Composable
fun AddDeviceScreen(
    container: AppContainer,
    onClose: () -> Unit,
    onProvisioned: () -> Unit,
    existingDevice: DeviceInfo? = null,
) {
    val updatingNetwork = existingDevice != null
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val provisioner = remember { BleProvisioner(context) }

    var devices by remember { mutableStateOf<List<FoundDevice>>(emptyList()) }
    var scanning by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<FoundDevice?>(null) }
    var ssid by rememberSaveable { mutableStateOf(currentSsid(context)) }
    var password by rememberSaveable { mutableStateOf("") }
    var deviceServer by rememberSaveable { mutableStateOf(container.session.serverUrl) }
    var status by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var permissionReady by remember { mutableStateOf(BleProvisioner.hasPermissions(context)) }

    fun startScan() {
        error = ""
        devices = emptyList()
        scanning = true
        provisioner.startScan(
            onFound = { found ->
                devices = (devices.filterNot { it.address == found.address } + found)
                    .sortedByDescending { it.rssi }
            },
            onError = { message ->
                scanning = false
                error = message
            },
        )
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {
        permissionReady = BleProvisioner.hasPermissions(context)
        if (permissionReady) {
            startScan()
        } else {
            error = "需要蓝牙权限才能扫描设备"
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            provisioner.stopScan()
            provisioner.close()
        }
    }

    fun startProvision() {
        val target = selected ?: return
        if (ssid.isBlank()) {
            error = "请填写 Wi-Fi 名称"
            return
        }
        if (!deviceServer.startsWith("http://") && !deviceServer.startsWith("https://")) {
            error = "服务器地址需以 http:// 或 https:// 开头"
            return
        }
        scope.launch {
            busy = true
            error = ""
            status = "正在连接设备…"
            try {
                val message = provisioner.provision(
                    device = target.device,
                    ssid = ssid.trim(),
                    password = password,
                    serverUrl = deviceServer.trim(),
                    expectedDeviceId = existingDevice?.id,
                    tokenProvider = { deviceId ->
                        if (updatingNetwork) container.api.createNetworkAuthorization(deviceId)
                        else container.api.createPairing(deviceId)
                    },
                    onState = { state ->
                        status = when (state) {
                            "gatt_connect" -> "正在建立 GATT 连接…"
                            "hello" -> "蓝牙已就绪，正在校验设备身份…"
                            "network_authorization" -> if (updatingNetwork) "正在申请一次性改网授权…" else "正在申请绑定授权…"
                            "write_config" -> "正在分帧写入网络配置…"
                            "config_ack" -> "正在等待设备确认配置…"
                            "wifi_connecting" -> "配置已写入，设备正在连接 Wi-Fi…"
                            "wifi_ok" -> if (updatingNetwork) "Wi-Fi 已连接，正在验证改网授权…" else "Wi-Fi 已连接，正在向服务器绑定…"
                            "authorization_retry" -> "改网授权暂未通过，设备正在重试…"
                            "claim_failed" -> "绑定失败，设备正在重试…"
                            "network_updated" -> "网络设置已更新"
                            "claimed" -> "绑定成功"
                            else -> state
                        }
                    },
                )
                status = message
                onProvisioned()
            } catch (exc: Exception) {
                error = exc.message ?: "配网失败"
                status = ""
            } finally {
                busy = false
            }
        }
    }

    BaScreen {
        Column(modifier = Modifier.fillMaxSize()) {
            BaTopBar(
                title = if (updatingNetwork) "网络设置" else "添加机器人",
                subtitle = if (updatingNetwork) "WIFI SETTINGS" else "BLE PROVISION",
                onBack = onClose,
            )

            Column(
                modifier = Modifier
                    .weight(1f)
                    .verticalScroll(rememberScrollState())
                    .imePadding()
                    .padding(horizontal = 14.dp)
                    .padding(top = 12.dp, bottom = 20.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                if (selected == null) {
                    if (updatingNetwork) {
                        Hint("正在为 ${existingDevice?.title} 修改网络。请长按机器人 BOOT 键 3 秒，看到 BLE 状态后扫描。")
                        Hint("只修改 Wi-Fi 和服务器地址，不解除绑定，也不会更换设备身份。")
                    } else {
                        Hint("给机器人通电后，手机会扫到名为 Agent-XXXX 的蓝牙设备。")
                    }
                    Hint("服务器：${container.session.serverUrl}")

                    if (!permissionReady) {
                        BaButton(
                            onClick = { permissionLauncher.launch(BleProvisioner.requiredPermissions()) },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text("授权蓝牙并开始扫描")
                        }
                    } else {
                        BaButton(
                            onClick = {
                                val problem = provisioner.issue()
                                if (problem != null) error = problem else startScan()
                            },
                            enabled = !scanning,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(if (scanning) "扫描中…" else "扫描附近设备")
                        }
                    }

                    if (scanning) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.Center,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(22.dp),
                                color = BaSkyDeep,
                                strokeWidth = 2.dp,
                            )
                        }
                    }

                    if (devices.isNotEmpty()) {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            devices.forEach { device ->
                                Surface(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .clickable {
                                            provisioner.stopScan()
                                            scanning = false
                                            selected = device
                                            error = ""
                                        },
                                    shape = rememberCutShape(12.dp),
                                    color = Color.White,
                                    border = BorderStroke(1.dp, BaLine),
                                ) {
                                    Row(
                                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 11.dp),
                                        verticalAlignment = Alignment.CenterVertically,
                                    ) {
                                        Box(
                                            modifier = Modifier
                                                .size(8.dp)
                                                .background(BaSky, CircleShape),
                                        )
                                        Spacer(Modifier.width(10.dp))
                                        Column(modifier = Modifier.weight(1f)) {
                                            Text(
                                                device.name,
                                                style = MaterialTheme.typography.titleSmall,
                                                color = BaNavy,
                                            )
                                            Text(
                                                "${device.address}｜信号 ${device.rssi} dBm",
                                                style = MaterialTheme.typography.bodySmall,
                                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            )
                                        }
                                        Text(
                                            "连接 ›",
                                            style = MaterialTheme.typography.labelSmall,
                                            color = BaSkyDeep,
                                        )
                                    }
                                }
                            }
                        }
                    } else if (scanning) {
                        Hint("正在扫描，请让机器人靠近手机。")
                    }
                } else {
                    val target = selected
                    SectionCard(title = if (updatingNetwork) "新的网络参数" else "配网参数") {
                        Hint("设备：${target?.name}（${target?.address}）")
                        if (updatingNetwork) Hint("将保留原设备绑定；若修改失败，机器人会自动恢复原网络并重新连接。")
                        BaTextField(
                            value = ssid,
                            onValueChange = { ssid = it },
                            label = { Text("Wi-Fi 名称") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        BaTextField(
                            value = password,
                            onValueChange = { password = it },
                            label = { Text("Wi-Fi 密码") },
                            singleLine = true,
                            visualTransformation = PasswordVisualTransformation(),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Hint("Wi-Fi 需为 2.4GHz；设备会连接 ${deviceServer}")
                        BaTextField(
                            value = deviceServer,
                            onValueChange = { deviceServer = it },
                            label = { Text("机器人访问的服务器地址") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )

                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            BaButton(
                                onClick = { startProvision() },
                                enabled = !busy,
                                modifier = Modifier.weight(1f),
                            ) {
                                Text(
                                    when {
                                        busy && updatingNetwork -> "正在修改…"
                                        busy -> "配网中…"
                                        updatingNetwork -> "保存并连接"
                                        else -> "开始配网"
                                    }
                                )
                            }
                            BaOutlineButton(
                                onClick = {
                                    selected = null
                                    status = ""
                                    error = ""
                                },
                                enabled = !busy,
                                modifier = Modifier.weight(1f),
                            ) {
                                Text("重新选择")
                            }
                        }
                    }
                }

                if (status.isNotBlank()) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(Color.White.copy(alpha = 0.8f))
                            .padding(horizontal = 12.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Box(
                            modifier = Modifier
                                .size(5.dp)
                                .background(BaSkyDeep, CircleShape),
                        )
                        Spacer(Modifier.width(8.dp))
                        Text(
                            status,
                            style = MaterialTheme.typography.bodySmall,
                            color = BaNavy,
                        )
                    }
                }
                if (error.isNotBlank()) {
                    Text(
                        error,
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }

                Spacer(Modifier.height(4.dp))
            }
        }
    }
}

private fun currentSsid(context: Context): String {
    return try {
        val manager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        val raw = manager?.connectionInfo?.ssid ?: ""
        raw.trim('"').takeIf { it.isNotBlank() && it != "<unknown ssid>" } ?: ""
    } catch (exc: Exception) {
        ""
    }
}
