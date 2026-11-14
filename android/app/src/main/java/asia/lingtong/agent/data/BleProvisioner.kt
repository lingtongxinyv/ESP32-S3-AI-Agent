package asia.lingtong.agent.data

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.util.UUID

data class FoundDevice(
    val name: String,
    val address: String,
    val rssi: Int,
    val device: BluetoothDevice,
)

/**
 * BLE 配网客户端：扫描广播、连接自定义 GATT、按「2 字节长度 + JSON」分帧收发。
 * 所有回调都切回主线程，调用方可以直接更新 Compose 状态。
 */
@SuppressLint("MissingPermission")
class BleProvisioner(context: Context) {

    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())

    private var scanCallback: ScanCallback? = null
    private var gatt: BluetoothGatt? = null
    private var rxChar: BluetoothGattCharacteristic? = null
    private var txChar: BluetoothGattCharacteristic? = null

    private val incoming = Channel<JSONObject>(Channel.UNLIMITED)
    private val buffer = ByteArrayOutputStream()
    private var mtu = 23
    private var ready: CompletableDeferred<Unit>? = null
    private var writeAck: CompletableDeferred<Unit>? = null
    private var lastStage = "蓝牙连接"

    private val btAdapter: BluetoothAdapter?
        get() = (appContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter

    fun issue(): String? {
        val adapter = btAdapter ?: return "本机不支持蓝牙"
        if (!adapter.isEnabled) return "请打开手机蓝牙"
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S && !locationEnabled(appContext)) {
            return "请打开定位开关（Android 12 以下扫描蓝牙需要）"
        }
        if (!hasPermissions(appContext)) return "缺少蓝牙权限"
        return null
    }

    fun startScan(onFound: (FoundDevice) -> Unit, onError: (String) -> Unit) {
        val problem = issue()
        if (problem != null) {
            onError(problem)
            return
        }
        val adapter = btAdapter ?: return
        if (scanCallback != null) return
        val scanner = adapter.bluetoothLeScanner ?: run {
            onError("无法使用 BLE 扫描")
            return
        }
        val filter = ScanFilter.Builder()
            .setServiceUuid(ParcelUuid(SERVICE_UUID))
            .build()
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()
        val callback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val found = FoundDevice(
                    name = result.scanRecord?.deviceName ?: result.device.name ?: "未命名设备",
                    address = result.device.address,
                    rssi = result.rssi,
                    device = result.device,
                )
                mainHandler.post { onFound(found) }
            }

            override fun onScanFailed(errorCode: Int) {
                scanCallback = null
                mainHandler.post { onError("扫描失败（错误码 $errorCode）") }
            }
        }
        scanCallback = callback
        scanner.startScan(listOf(filter), settings, callback)
    }

    fun stopScan() {
        val callback = scanCallback ?: return
        scanCallback = null
        runCatching { btAdapter?.bluetoothLeScanner?.stopScan(callback) }
    }

    /**
     * 连接设备并完成配网：hello 取设备 ID → 申请配对令牌 → 写入 Wi-Fi → 等 claimed。
     * 失败抛 IOException，调用方负责展示。
     */
    suspend fun provision(
        device: BluetoothDevice,
        ssid: String,
        password: String,
        serverUrl: String,
        expectedDeviceId: String? = null,
        tokenProvider: suspend (String) -> String,
        onState: (String) -> Unit,
    ): String {
        issue()?.let { throw IOException(it) }
        stopScan()

        val readySignal = CompletableDeferred<Unit>()
        ready = readySignal
        buffer.reset()
        while (incoming.tryReceive().isSuccess) {
            // 丢弃上一次会话残留的消息
        }
        mtu = 23
        lastStage = "蓝牙连接"
        onState("gatt_connect")

        val connection = device.connectGatt(appContext, false, callback, BluetoothDevice.TRANSPORT_LE)
            ?: throw IOException("无法发起蓝牙连接")
        gatt = connection
        try {
            return withTimeout(130_000) {
                lastStage = "服务发现与 MTU 协商"
                withTimeout(20_000) { readySignal.await() }

                lastStage = "设备身份校验"
                onState("hello")
                writeFrame(JSONObject().put("cmd", "hello").toString())
                val hello = awaitMessage(8_000) { it.has("device_id") }
                val deviceId = hello.optString("device_id")
                if (deviceId.isBlank()) throw IOException("设备没有返回 ID")
                if (expectedDeviceId != null && deviceId != expectedDeviceId) {
                    throw IOException("蓝牙设备与所选机器人不一致，请重新选择")
                }

                lastStage = if (expectedDeviceId == null) "申请绑定授权" else "申请改网授权"
                onState("network_authorization")
                val token = tokenProvider(deviceId)
                lastStage = "分帧写入配置"
                onState("write_config")
                val wifi = JSONObject()
                    .put("cmd", "wifi")
                    .put("wifi_ssid", ssid)
                    .put("wifi_password", password)
                    .put("server_url", serverUrl)
                    .put("pairing_token", token)
                writeFrame(wifi.toString())

                lastStage = "等待配置 ACK"
                onState("config_ack")
                val ack = awaitMessage(8_000) { it.has("ok") }
                if (!ack.optBoolean("ok")) {
                    throw IOException("设备拒绝配置：${ack.optString("error")}")
                }

                lastStage = "设备连接 WiFi"
                while (true) {
                    val message = awaitMessage(125_000)
                    if (message.optString("event") != "state") continue
                    val state = message.optString("state")
                    lastStage = when (state) {
                        "wifi_ok", "authorization_retry" -> "网络校时与改网授权"
                        "wifi_connecting" -> "设备连接 WiFi"
                        else -> lastStage
                    }
                    mainHandler.post { onState(state) }
                    when (state) {
                        "claimed" -> return@withTimeout "配网成功，设备已上线"
                        "network_updated" -> return@withTimeout "网络设置已更新，设备正在重新连接"
                        "wifi_config_failed", "wifi_timeout", "authorization_failed",
                        "authorization_timeout", "storage_failed" -> {
                            val detail = message.optString("message")
                            throw IOException(
                                "${wifiFailureText(state)}${if (detail.isBlank()) "" else "（$detail）"}；机器人已恢复原网络"
                            )
                        }
                        "error" -> throw IOException(message.optString("message").ifBlank { "设备网络设置失败" })
                    }
                }
                @Suppress("UNREACHABLE_CODE")
                throw IOException("设备未返回结果")
            }
        } catch (exc: TimeoutCancellationException) {
            throw IOException("操作超时，最后阶段：$lastStage")
        } finally {
            close()
        }
    }

    fun close() {
        runCatching { gatt?.disconnect() }
        runCatching { gatt?.close() }
        gatt = null
        rxChar = null
        txChar = null
        ready = null
        writeAck = null
    }

    private suspend fun awaitMessage(
        timeoutMs: Long,
        predicate: (JSONObject) -> Boolean = { true },
    ): JSONObject = try {
        withTimeout(timeoutMs) {
            var result: JSONObject? = null
            while (result == null) {
                val message = incoming.receive()
                if (predicate(message)) result = message
            }
            result!!
        }
    } catch (exc: TimeoutCancellationException) {
        throw IOException("等待设备响应超时，最后阶段：$lastStage")
    }

    private suspend fun writeFrame(json: String) {
        val payload = json.toByteArray(Charsets.UTF_8)
        if (payload.size > 1024) throw IOException("配置内容过长")
        val frame = ByteArray(payload.size + 2)
        frame[0] = (payload.size and 0xFF).toByte()
        frame[1] = ((payload.size shr 8) and 0xFF).toByte()
        payload.copyInto(frame, 2)
        val chunkSize = (mtu - 3).coerceIn(20, 244)
        var offset = 0
        while (offset < frame.size) {
            val size = minOf(chunkSize, frame.size - offset)
            writeChunk(frame.copyOfRange(offset, offset + size))
            offset += size
        }
    }

    private suspend fun writeChunk(chunk: ByteArray) {
        val connection = gatt ?: throw IOException("蓝牙已断开")
        val characteristic = rxChar ?: throw IOException("配网服务未就绪")
        val ack = CompletableDeferred<Unit>()
        writeAck = ack
        characteristic.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        characteristic.value = chunk
        if (!connection.writeCharacteristic(characteristic)) {
            writeAck = null
            throw IOException("写入蓝牙特征失败")
        }
        try {
            withTimeout(5_000) { ack.await() }
        } catch (exc: TimeoutCancellationException) {
            writeAck = null
            throw IOException("蓝牙写入超时")
        }
        writeAck = null
    }

    private fun handleData(data: ByteArray) {
        if (data.isEmpty()) return
        buffer.write(data)
        val bytes = buffer.toByteArray()
        var offset = 0
        while (bytes.size - offset >= 2) {
            val length = (bytes[offset].toInt() and 0xFF) or ((bytes[offset + 1].toInt() and 0xFF) shl 8)
            if (length == 0 || length > 1024) {
                buffer.reset()
                return
            }
            if (bytes.size - offset - 2 < length) break
            val payload = String(bytes, offset + 2, length, Charsets.UTF_8)
            offset += 2 + length
            runCatching { JSONObject(payload) }.getOrNull()?.let { incoming.trySend(it) }
        }
        val remaining = bytes.copyOfRange(offset, bytes.size)
        buffer.reset()
        buffer.write(remaining)
    }

    private fun requestMtu() {
        val connection = gatt
        if (connection == null || !connection.requestMtu(247)) {
            ready?.complete(Unit)
        }
    }

    private val callback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                if (!g.discoverServices()) {
                    ready?.completeExceptionally(IOException("无法发起 BLE 服务发现"))
                }
            } else {
                runCatching { g.close() }
                if (gatt === g) gatt = null
                val detail = if (status == BluetoothGatt.GATT_SUCCESS) "蓝牙连接已断开" else "GATT 连接失败（$status）"
                ready?.completeExceptionally(IOException(detail))
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            val service = g.getService(SERVICE_UUID)
            val rx = service?.getCharacteristic(RX_UUID)
            val tx = service?.getCharacteristic(TX_UUID)
            if (status != BluetoothGatt.GATT_SUCCESS || rx == null || tx == null) {
                ready?.completeExceptionally(IOException("设备不支持配网服务"))
                return
            }
            rxChar = rx
            txChar = tx
            g.setCharacteristicNotification(tx, true)
            val descriptor = tx.getDescriptor(CCCD_UUID)
            if (descriptor == null) {
                requestMtu()
            } else {
                descriptor.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                if (!g.writeDescriptor(descriptor)) requestMtu()
            }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            requestMtu()
        }

        override fun onMtuChanged(g: BluetoothGatt, mtuValue: Int, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) mtu = mtuValue
            ready?.complete(Unit)
        }

        override fun onCharacteristicWrite(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int,
        ) {
            val ack = writeAck
            writeAck = null
            if (status == BluetoothGatt.GATT_SUCCESS) {
                ack?.complete(Unit)
            } else {
                ack?.completeExceptionally(IOException("蓝牙写入失败（$status）"))
            }
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            handleData(characteristic.value ?: ByteArray(0))
        }

        @RequiresApi(Build.VERSION_CODES.TIRAMISU)
        override fun onCharacteristicChanged(
            g: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
        ) {
            handleData(value)
        }
    }

    companion object {
        val SERVICE_UUID: UUID = UUID.fromString("7f1e0001-3c4a-4bd8-9f1e-6a1b2c3d4e5f")
        private val RX_UUID: UUID = UUID.fromString("7f1e0002-3c4a-4bd8-9f1e-6a1b2c3d4e5f")
        private val TX_UUID: UUID = UUID.fromString("7f1e0003-3c4a-4bd8-9f1e-6a1b2c3d4e5f")
        private val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

        fun requiredPermissions(): Array<String> = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }

        fun hasPermissions(context: Context): Boolean = requiredPermissions().all {
            ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
        }

        fun locationEnabled(context: Context): Boolean {
            val manager = context.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
                ?: return false
            return manager.isProviderEnabled(LocationManager.GPS_PROVIDER) ||
                manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        }
    }
}

private fun wifiFailureText(state: String): String = when (state) {
    "wifi_config_failed" -> "WiFi 配置失败"
    "wifi_timeout" -> "WiFi 连接超时"
    "authorization_failed" -> "改网授权失败"
    "authorization_timeout" -> "网络校时或改网授权超时"
    "storage_failed" -> "设备配置保存失败"
    else -> "网络修改失败"
}
