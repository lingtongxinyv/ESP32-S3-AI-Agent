#include "network.h"

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include "app_config.h"
#include "app_control.h"
#include "cJSON.h"
#include "esp_check.h"
#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "wake_word_detector.h"

namespace {
constexpr char kTag[] = "network";
constexpr uint8_t kWsBinaryOpcode = 0x2;
constexpr uint8_t kWsTextOpcode = 0x1;

uint32_t read_le32(const uint8_t *p)
{
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

void write_le32(uint8_t *p, uint32_t value)
{
    p[0] = value & 0xff;
    p[1] = (value >> 8) & 0xff;
    p[2] = (value >> 16) & 0xff;
    p[3] = value >> 24;
}

uint32_t json_session(const cJSON *root)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, "session");
    if (cJSON_IsNumber(value) && value->valuedouble >= 0) {
        return static_cast<uint32_t>(value->valuedouble);
    }
    if (cJSON_IsString(value) && value->valuestring) {
        return static_cast<uint32_t>(std::strtoul(value->valuestring, nullptr, 10));
    }
    return 0;
}

const char *json_text(const cJSON *root, const char *first, const char *second)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(root, first);
    if (!cJSON_IsString(value)) value = cJSON_GetObjectItemCaseSensitive(root, second);
    return cJSON_IsString(value) && value->valuestring ? value->valuestring : "";
}

void on_time_sync(timeval *)
{
    const time_t now = time(nullptr);
    tm local = {};
    localtime_r(&now, &local);
    char text[32] = {};
    std::strftime(text, sizeof(text), "%Y-%m-%d %H:%M:%S", &local);
    ESP_LOGI(kTag, "SNTP synchronized: %s UTC+8", text);
}
} // namespace

esp_err_t Network::init(const DeviceConfig &config)
{
    config_ = config;
    ws_mutex_ = xSemaphoreCreateMutex();
    if (!ws_mutex_) return ESP_ERR_NO_MEM;

    // The upstream component can print the complete URI, including our query token.
    esp_log_level_set("websocket_client", ESP_LOG_ERROR);
    esp_log_level_set("transport_ws", ESP_LOG_NONE);
    esp_log_level_set("esp_http_client", ESP_LOG_NONE);

    ESP_RETURN_ON_ERROR(esp_netif_init(), kTag, "netif init");
    esp_err_t err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;
    netif_ = esp_netif_create_default_wifi_sta();
    if (!netif_) return ESP_FAIL;

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&init_config), kTag, "WiFi init");
    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, this, nullptr), kTag, "WiFi events");
    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, this, nullptr), kTag, "IP events");
    ESP_RETURN_ON_ERROR(esp_wifi_set_storage(WIFI_STORAGE_RAM), kTag, "WiFi storage");
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), kTag, "WiFi mode");

    wifi_config_t wifi_config = {};
    std::snprintf(reinterpret_cast<char *>(wifi_config.sta.ssid), sizeof(wifi_config.sta.ssid), "%s", config_.wifi_ssid.c_str());
    std::snprintf(reinterpret_cast<char *>(wifi_config.sta.password), sizeof(wifi_config.sta.password), "%s", config_.wifi_password.c_str());
    wifi_config.sta.pmf_cfg.capable = true;
    wifi_config.sta.pmf_cfg.required = false;
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &wifi_config), kTag, "WiFi config");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), kTag, "WiFi start");
    ESP_RETURN_ON_ERROR(esp_wifi_set_ps(WIFI_PS_NONE), kTag, "WiFi power save");
    wifi_initialized_ = true;
    if (xTaskCreate(tx_task_entry, "audio_net_tx", 6144, this, 6, nullptr) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    // esp_wifi_start() 之后 STA 往往已经开始自动连接，此时再调 connect 会返回
    // ESP_ERR_WIFI_STATE / ESP_ERR_WIFI_CONN（已在连接中）。这属于正常情况，不能当失败处理。
    const esp_err_t connect_err = esp_wifi_connect();
    return (connect_err == ESP_ERR_WIFI_STATE || connect_err == ESP_ERR_WIFI_CONN) ? ESP_OK : connect_err;
}

void Network::set_device_token(const std::string &token)
{
    stop_websocket();
    config_.device_token = token;
}

esp_err_t Network::update_config(const DeviceConfig &config)
{
    stop_websocket();
    config_ = config;
    esp_err_t err = esp_wifi_disconnect();
    if (err != ESP_OK && err != ESP_ERR_WIFI_NOT_CONNECT) {
        last_wifi_error_.store(err);
        ESP_LOGW(kTag, "WiFi disconnect before update failed: %s", esp_err_to_name(err));
        return err;
    }
    wifi_config_t wifi_config = {};
    std::snprintf(reinterpret_cast<char *>(wifi_config.sta.ssid), sizeof(wifi_config.sta.ssid), "%s", config_.wifi_ssid.c_str());
    std::snprintf(reinterpret_cast<char *>(wifi_config.sta.password), sizeof(wifi_config.sta.password), "%s", config_.wifi_password.c_str());
    wifi_config.sta.pmf_cfg.capable = true;
    wifi_config.sta.pmf_cfg.required = false;
    err = esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    if (err != ESP_OK) {
        last_wifi_error_.store(err);
        ESP_LOGW(kTag, "WiFi set update config failed: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_wifi_connect();
    if (err == ESP_ERR_WIFI_CONN) err = ESP_OK;
    last_wifi_error_.store(err);
    if (err != ESP_OK) ESP_LOGW(kTag, "WiFi connect updated config failed: %s", esp_err_to_name(err));
    return err;
}

esp_err_t Network::reconnect_wifi()
{
    if (!wifi_initialized_) return ESP_ERR_WIFI_NOT_INIT;
    const esp_err_t err = esp_wifi_connect();
    last_wifi_error_.store(err == ESP_ERR_WIFI_CONN ? ESP_OK : err);
    return err == ESP_ERR_WIFI_CONN ? ESP_OK : err;
}

void Network::start_sntp()
{
    // POSIX TZ 的符号与日常表达相反：CST-8 表示固定 UTC+8，且无夏令时。
    setenv("TZ", "CST-8", 1);
    tzset();
    if (sntp_initialized_) {
        esp_netif_sntp_start();
        return;
    }
    esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("ntp.aliyun.com");
    config.wait_for_sync = false;
    config.start = true;
    config.sync_cb = on_time_sync;
    if (esp_netif_sntp_init(&config) == ESP_OK) {
        sntp_initialized_ = true;
        ESP_LOGI(kTag, "SNTP started (Beijing UTC+8)");
    }
}

bool Network::time_synchronized() const
{
    const time_t now = time(nullptr);
    return now > 1700000000;
}

esp_err_t Network::start_websocket()
{
    if (ws_connected_.load()) return ESP_OK;
    if (!wifi_connected_.load()) return ESP_ERR_WIFI_NOT_CONNECT;
    const bool secure = config_.server_url.rfind("https://", 0) == 0 || config_.server_url.rfind("wss://", 0) == 0;
    if (secure && !time_synchronized()) return ESP_ERR_INVALID_STATE;

    if (xSemaphoreTake(ws_mutex_, pdMS_TO_TICKS(2000)) != pdPASS) return ESP_ERR_TIMEOUT;
    if (ws_) {
        esp_websocket_client_stop(ws_);
        esp_websocket_client_destroy(ws_);
        ws_ = nullptr;
    }
    ws_uri_ = websocket_url(config_);
    // cpolar WS Upgrade 剥 Authorization，但透传 Sec-WebSocket-Protocol。
    // 子协议名必须符合 RFC 7230 token 规则；token_urlsafe(32) 输出的 A-Za-z0-9_- 满足。
    ws_subprotocol_ = "bearer." + config_.device_token;
    esp_websocket_client_config_t config = {};
    config.uri = ws_uri_.c_str();
    config.subprotocol = ws_subprotocol_.c_str();
    config.disable_auto_reconnect = true;
    config.task_prio = 5;
    config.task_name = "ws_client";
    config.task_stack = 8192;
    config.buffer_size = 2048;
    config.network_timeout_ms = 1000;
    config.ping_interval_sec = 15;
    config.pingpong_timeout_sec = 45;
    config.keep_alive_enable = true;
    config.keep_alive_idle = 10;
    config.keep_alive_interval = 5;
    config.keep_alive_count = 3;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    config.transport = secure ? WEBSOCKET_TRANSPORT_OVER_SSL : WEBSOCKET_TRANSPORT_OVER_TCP;
    stopping_ws_.store(false);
    ws_ = esp_websocket_client_init(&config);
    if (!ws_) {
        xSemaphoreGive(ws_mutex_);
        return ESP_ERR_NO_MEM;
    }
    esp_websocket_register_events(ws_, WEBSOCKET_EVENT_ANY, websocket_event_handler, this);
    esp_err_t err = esp_websocket_client_start(ws_);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "ws start failed: %s", esp_err_to_name(err));
        esp_websocket_client_destroy(ws_);
        ws_ = nullptr;
    } else {
        ESP_LOGI(kTag, "WebSocket connecting (credential in Sec-WebSocket-Protocol)");
    }
    xSemaphoreGive(ws_mutex_);
    return err;
}

void Network::stop_websocket()
{
    stopping_ws_.store(true);
    ws_connected_.store(false);
    if (ws_mutex_ && xSemaphoreTake(ws_mutex_, pdMS_TO_TICKS(2500)) == pdPASS) {
        auto *client = ws_;
        ws_ = nullptr;
        if (client) {
            esp_websocket_client_stop(client);
            esp_websocket_client_destroy(client);
        }
        xSemaphoreGive(ws_mutex_);
    }
}

bool Network::send_hello()
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return false;
    cJSON_AddNumberToObject(root, "v", config::kProtocolVersion);
    cJSON_AddStringToObject(root, "t", "hello");
    cJSON_AddNumberToObject(root, "session", 0);
    cJSON_AddStringToObject(root, "device_id", config_.device_id.c_str());
    cJSON_AddStringToObject(root, "fw_version", config::kFirmwareVersion);
    cJSON_AddNumberToObject(root, "volume", audio_.volume());
    cJSON_AddBoolToObject(root, "panel_auto", true);
    return send_json(root);
}

bool Network::send_session_message(const char *type, uint32_t session)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return false;
    cJSON_AddNumberToObject(root, "v", config::kProtocolVersion);
    cJSON_AddStringToObject(root, "t", type);
    cJSON_AddNumberToObject(root, "session", static_cast<double>(session));
    return send_json(root);
}

bool Network::send_start(uint32_t session)
{
    // 服务端 handle_start 校验 sample_rate 必须为 16000，history 可选（设备端历史留待后续实现）
    cJSON *root = cJSON_CreateObject();
    if (!root) return false;
    cJSON_AddNumberToObject(root, "v", config::kProtocolVersion);
    cJSON_AddStringToObject(root, "t", "start");
    cJSON_AddNumberToObject(root, "session", static_cast<double>(session));
    cJSON_AddNumberToObject(root, "sample_rate", config::kSampleRate);
    return send_json(root);
}

bool Network::send_ping(uint32_t session)
{
    return send_session_message("ping", session);
}

bool Network::send_panel_ack(bool ok, int volume, const char *error)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return false;
    cJSON_AddNumberToObject(root, "v", config::kProtocolVersion);
    cJSON_AddStringToObject(root, "t", "panel_ack");
    cJSON_AddNumberToObject(root, "session", 0);
    cJSON_AddBoolToObject(root, "ok", ok);
    cJSON_AddNumberToObject(root, "volume", volume);
    if (!ok && error && *error) cJSON_AddStringToObject(root, "error", error);
    return send_json(root);
}

bool Network::send_config_ack(uint32_t version, bool ok, const char *error)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return false;
    cJSON_AddNumberToObject(root, "v", config::kProtocolVersion);
    cJSON_AddStringToObject(root, "t", "config_ack");
    cJSON_AddNumberToObject(root, "session", 0);
    cJSON_AddNumberToObject(root, "version", static_cast<double>(version));
    cJSON_AddBoolToObject(root, "ok", ok);
    if (!ok && error && *error) cJSON_AddStringToObject(root, "error", error);
    return send_json(root);
}

bool Network::send_json(cJSON *root)
{
    char *text = cJSON_PrintUnformatted(root);
    if (!text) {
        cJSON_Delete(root);
        return false;
    }
    const size_t length = std::strlen(text);
    const bool ok = send_text(text, length);
    if (!ok) {
        const cJSON *type = cJSON_GetObjectItemCaseSensitive(root, "t");
        ESP_LOGW(kTag, "websocket text send failed: type=%s length=%u",
                 cJSON_IsString(type) ? type->valuestring : "unknown", (unsigned)length);
    }
    cJSON_Delete(root);
    cJSON_free(text);
    return ok;
}

bool Network::send_text(const char *data, size_t length)
{
    if (!ws_connected_.load() || xSemaphoreTake(ws_mutex_, pdMS_TO_TICKS(250)) != pdPASS) return false;
    auto *client = ws_;
    const int sent = client ? esp_websocket_client_send_text(client, data, length, pdMS_TO_TICKS(250)) : -1;
    xSemaphoreGive(ws_mutex_);
    return sent == static_cast<int>(length);
}

void Network::wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    auto *self = static_cast<Network *>(arg);
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const auto *disconnected = static_cast<const wifi_event_sta_disconnected_t *>(data);
        const int reason = disconnected ? disconnected->reason : 0;
        self->last_disconnect_reason_.store(reason);
        self->wifi_connected_.store(false);
        self->ws_connected_.store(false);
        ESP_LOGW(kTag, "wifi disconnected, reason=%d", reason);
        app::post_event(app::EventType::WifiDisconnected);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        self->wifi_connected_.store(true);
        app::post_event(app::EventType::WifiConnected);
    }
}

void Network::websocket_event_handler(void *arg, esp_event_base_t, int32_t id, void *data)
{
    auto *self = static_cast<Network *>(arg);
    auto *event = static_cast<esp_websocket_event_data_t *>(data);
    if (id == WEBSOCKET_EVENT_CONNECTED) {
        self->ws_connected_.store(true);
        ESP_LOGI(kTag, "websocket connected");
        app::post_event(app::EventType::WsConnected);
    } else if (id == WEBSOCKET_EVENT_DISCONNECTED || id == WEBSOCKET_EVENT_ERROR || id == WEBSOCKET_EVENT_CLOSED) {
        const bool was_connected = self->ws_connected_.exchange(false);
        (void)was_connected;
        ESP_LOGW(kTag,
                 "websocket down: event=%d http_status=%d tls_err=0x%x error_type=%d sock_errno=%d close_code=%d",
                 static_cast<int>(id),
                 event ? event->error_handle.esp_ws_handshake_status_code : -1,
                 event ? static_cast<unsigned>(event->error_handle.esp_tls_last_esp_err) : 0U,
                 event ? static_cast<int>(event->error_handle.error_type) : -1,
                 event && id == WEBSOCKET_EVENT_DISCONNECTED ? event->error_handle.esp_transport_sock_errno : 0,
                 event ? event->close_status_code : -1);
        if (!self->stopping_ws_.load()) {
            app::post_event(app::EventType::WsDisconnected);
        }
    } else if (id == WEBSOCKET_EVENT_DATA && event) {
        self->handle_websocket_data(*event);
    }
}

void Network::handle_websocket_data(const esp_websocket_event_data_t &event)
{
    if (event.op_code != kWsTextOpcode && event.op_code != kWsBinaryOpcode && event.op_code != 0) return;
    if (event.payload_offset == 0) {
        rx_message_.clear();
        rx_expected_ = event.payload_len;
        rx_opcode_ = event.op_code;
        const size_t maximum = rx_opcode_ == kWsBinaryOpcode ?
            config::kBinaryHeaderBytes + config::kMonoFrameBytes : config::kMaxJsonMessage;
        if (rx_expected_ < 0 || static_cast<size_t>(rx_expected_) > maximum) {
            rx_expected_ = 0;
            return;
        }
        rx_message_.resize(rx_expected_);
    }
    if (rx_expected_ == 0 || event.data_len < 0 || event.payload_offset < 0 ||
        event.payload_offset + event.data_len > rx_expected_) {
        rx_message_.clear();
        rx_expected_ = 0;
        return;
    }
    if (event.data_len > 0) {
        std::memcpy(rx_message_.data() + event.payload_offset, event.data_ptr, event.data_len);
    }
    if (event.payload_offset + event.data_len != rx_expected_) return;
    if (rx_opcode_ == kWsTextOpcode) {
        handle_json(rx_message_.data(), rx_message_.size());
    } else {
        handle_binary(reinterpret_cast<const uint8_t *>(rx_message_.data()), rx_message_.size());
    }
    rx_message_.clear();
    rx_expected_ = 0;
}

void Network::handle_json(const char *data, size_t length)
{
    cJSON *root = cJSON_ParseWithLength(data, length);
    if (!root) return;
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "v");
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(root, "t");
    if (!cJSON_IsNumber(version) || version->valueint != config::kProtocolVersion || !cJSON_IsString(type)) {
        cJSON_Delete(root);
        return;
    }
    const std::string name = type->valuestring;
    const uint32_t session = json_session(root);
    if (name == "asr_result") {
        app::post_event(app::EventType::AsrResult, session, 0, json_text(root, "text", "delta"));
    } else if (name == "llm_delta") {
        app::post_event(app::EventType::LlmDelta, session, 0, json_text(root, "delta", "text"));
    } else if (name == "audio_start") {
        audio_.start_playback(session);
        app::post_event(app::EventType::AudioStart, session);
    } else if (name == "audio_end") {
        audio_.end_playback(session);
        app::post_event(app::EventType::AudioEnd, session);
    } else if (name == "done") {
        app::post_event(app::EventType::Done, session);
    } else if (name == "ota_check") {
        app::post_event(app::EventType::OtaCheck);
    } else if (name == "error") {
        app::post_event(app::EventType::ServerError, session, 0, json_text(root, "message", "error"));
    } else if (name == "config") {
        const cJSON *number = cJSON_GetObjectItemCaseSensitive(root, "version");
        const uint32_t config_version = cJSON_IsNumber(number) && number->valuedouble >= 1 &&
                                                number->valuedouble <= 1000000
                                            ? static_cast<uint32_t>(number->valuedouble)
                                            : 0;
        const cJSON *bundle = cJSON_GetObjectItemCaseSensitive(root, "bundle");
        const cJSON *wake_word = cJSON_GetObjectItemCaseSensitive(bundle, "wake_word");
        const cJSON *wake_command = cJSON_GetObjectItemCaseSensitive(bundle, "wake_command");
        bool ok = config_version != 0 && cJSON_IsObject(bundle);
        const char *error = ok ? nullptr : "invalid_config";
        // 旧服务端可能完全不带唤醒配置：其余配置仍确认，保留当前识别词。
        if (ok && wake_word != nullptr) {
            const char *text = cJSON_IsString(wake_word) ? wake_word->valuestring : nullptr;
            size_t begin = 0;
            size_t end = text ? std::strlen(text) : 0;
            while (begin < end && std::isspace(static_cast<unsigned char>(text[begin]))) ++begin;
            while (end > begin && std::isspace(static_cast<unsigned char>(text[end - 1]))) --end;
            const std::string value = text ? std::string(text + begin, text + end) : std::string();
            const std::string command = cJSON_IsString(wake_command) && wake_command->valuestring
                ? wake_command->valuestring : std::string();
            if (value.empty() || value.size() > 96) {
                ok = false;
                error = "invalid_wake_word";
            } else if (command.empty()) {
                ok = false;
                error = "invalid_wake_command";
            } else {
                const esp_err_t wake_err = wake_.update_wake_word(value, command, &error);
                ok = wake_err == ESP_OK;
            }
        }
        send_config_ack(config_version, ok, error);
    } else if (name == "panel") {
        const char *emotion = json_text(root, "emotion", "emo");
        if (cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, "auto"))) {
            const cJSON *ttl = cJSON_GetObjectItemCaseSensitive(root, "ttl_ms");
            uint32_t ttl_ms = cJSON_IsNumber(ttl) && ttl->valuedouble > 0 && ttl->valuedouble <= 90000
                                  ? static_cast<uint32_t>(ttl->valuedouble)
                                  : 90000;
            app::post_event(app::EventType::PanelAuto, session, ttl_ms, emotion);
        } else {
            // 手动 payload = "emotion|text"；clear=true 时 emotion 位置写 "clear"，value 携带音量。
            const cJSON *vol = cJSON_GetObjectItemCaseSensitive(root, "volume");
            const uint32_t value = cJSON_IsNumber(vol) && vol->valueint >= 0 && vol->valueint <= 100
                                       ? static_cast<uint32_t>(vol->valueint)
                                       : 0xFFFFFFFFu;
            if (cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, "clear"))) emotion = "clear";
            char payload[192];
            std::snprintf(payload, sizeof(payload), "%s|%s", emotion, json_text(root, "text", "line"));
            app::post_event(app::EventType::Panel, 0, value, payload);
        }
    }
    cJSON_Delete(root);
}

void Network::handle_binary(const uint8_t *data, size_t length)
{
    if (!data || length != config::kBinaryHeaderBytes + config::kMonoFrameBytes ||
        data[0] != config::kProtocolVersion || data[1] != 2) {
        return;
    }
    const uint32_t session = read_le32(data + 4);
    const uint32_t sequence = read_le32(data + 8);
    const uint32_t payload_bytes = read_le32(data + 12);
    if (payload_bytes != config::kMonoFrameBytes) return;
    audio_.enqueue_downlink(session, sequence, data + config::kBinaryHeaderBytes, payload_bytes);
}

void Network::tx_task_entry(void *arg)
{
    static_cast<Network *>(arg)->tx_task();
}

void Network::tx_task()
{
    uint8_t packet[config::kBinaryHeaderBytes + config::kMonoFrameBytes];
    packet[0] = config::kProtocolVersion;
    packet[1] = 1;
    packet[2] = packet[3] = 0;
    write_le32(packet + 12, config::kMonoFrameBytes);
    for (;;) {
        AudioFrame *frame = nullptr;
        if (!audio_.dequeue_uplink(&frame, portMAX_DELAY)) continue;
        bool sent = false;
        if (ws_connected_.load() &&
            (app::state() == app::State::LISTENING || app::state() == app::State::THINKING) &&
            frame->session == app::session()) {
            write_le32(packet + 4, frame->session);
            write_le32(packet + 8, frame->sequence);
            std::memcpy(packet + config::kBinaryHeaderBytes, frame->pcm, config::kMonoFrameBytes);
            if (xSemaphoreTake(ws_mutex_, pdMS_TO_TICKS(250)) == pdPASS) {
                auto *client = ws_;
                sent = client && esp_websocket_client_send_bin(
                    client, reinterpret_cast<const char *>(packet), sizeof(packet), pdMS_TO_TICKS(300)) == sizeof(packet);
                xSemaphoreGive(ws_mutex_);
            }
            if (!sent) {
                ESP_LOGW(kTag, "websocket audio send failed: session=%u sequence=%u",
                         frame->session, frame->sequence);
            }
        }
        audio_.release_uplink(frame);
    }
}
