#include "provisioning.h"

#include <cstring>
#include "app_config.h"
#include "cJSON.h"
#include "driver/uart.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "provision";
constexpr size_t kMaxPayload = 2048;
constexpr uart_port_t kUart = UART_NUM_0;

uint16_t crc16_update(uint16_t crc, uint8_t value)
{
    crc ^= static_cast<uint16_t>(value) << 8;
    for (int i = 0; i < 8; ++i) {
        crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021) : static_cast<uint16_t>(crc << 1);
    }
    return crc;
}

uint16_t crc16_frame(uint8_t type, uint16_t length, const uint8_t *payload)
{
    uint16_t crc = 0xffff;
    crc = crc16_update(crc, config::kProtocolVersion);
    crc = crc16_update(crc, type);
    crc = crc16_update(crc, length & 0xff);
    crc = crc16_update(crc, length >> 8);
    for (size_t i = 0; i < length; ++i) crc = crc16_update(crc, payload[i]);
    return crc;
}

bool read_exact(uint8_t *data, size_t length, TickType_t timeout)
{
    size_t done = 0;
    const TickType_t started = xTaskGetTickCount();
    while (done < length) {
        TickType_t wait = timeout;
        if (timeout != portMAX_DELAY) {
            const TickType_t elapsed = xTaskGetTickCount() - started;
            if (elapsed >= timeout) return false;
            wait = timeout - elapsed;
        }
        const int count = uart_read_bytes(kUart, data + done, length - done, wait);
        if (count < 0) return false;
        done += static_cast<size_t>(count);
    }
    return true;
}

struct HttpResponse {
    std::string body;
    bool overflow = false;
};

esp_err_t http_data_handler(esp_http_client_event_t *event)
{
    auto *response = static_cast<HttpResponse *>(event->user_data);
    if (event->event_id == HTTP_EVENT_ON_DATA && response && event->data_len > 0) {
        if (response->body.size() + event->data_len > config::kMaxHttpResponse) {
            response->overflow = true;
        } else {
            response->body.append(static_cast<const char *>(event->data), event->data_len);
        }
    }
    return ESP_OK;
}

bool valid_server_url(const std::string &url)
{
    return (url.rfind("https://", 0) == 0 || url.rfind("http://", 0) == 0 ||
            url.rfind("wss://", 0) == 0 || url.rfind("ws://", 0) == 0) &&
           url.find_first_of("?#") == std::string::npos;
}
} // namespace

esp_err_t Provisioning::begin()
{
    // BLE 是主配网通道，保留日志便于排查；仅静音会刷屏的网络组件。
    esp_log_level_set("websocket_client", ESP_LOG_NONE);
    esp_log_level_set("transport_ws", ESP_LOG_NONE);
    esp_log_level_set("esp_http_client", ESP_LOG_NONE);

    if (!uart_is_driver_installed(kUart)) {
        uart_config_t config = {};
        config.baud_rate = 115200;
        config.data_bits = UART_DATA_8_BITS;
        config.parity = UART_PARITY_DISABLE;
        config.stop_bits = UART_STOP_BITS_1;
        config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
        config.source_clk = UART_SCLK_DEFAULT;
        if (uart_param_config(kUart, &config) != ESP_OK ||
            uart_set_pin(kUart, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) != ESP_OK ||
            uart_driver_install(kUart, 4096, 2048, 0, nullptr, 0) != ESP_OK) {
            return ESP_FAIL;
        }
    }
    uart_flush_input(kUart);
    return ESP_OK;
}

void Provisioning::restore_logs()
{
    esp_log_level_set("*", ESP_LOG_INFO);
}

esp_err_t Provisioning::try_receive(PendingConfig &pending, uint32_t timeout_ms)
{
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    for (;;) {
        uint8_t type = 0;
        std::string payload;
        const esp_err_t err = read_frame(type, payload, pdMS_TO_TICKS(100));
        if (err == ESP_OK) {
            if (type == 1) {
                if (payload.empty()) send_info(); else send_nack("bad_get_info");
            } else if (type == 3) {
                if (!parse_set_config(payload, pending)) {
                    send_nack("bad_config");
                } else {
                    pending.config.device_id = DeviceConfigStore::make_device_id();
                    pending.config.device_token.clear();
                    send_frame(4, "{\"ok\":true}");
                    uart_flush_input(kUart);
                    return ESP_OK;
                }
            } else {
                send_nack("unsupported_type");
            }
        }
        if (static_cast<int32_t>(xTaskGetTickCount() - deadline) >= 0) {
            return ESP_ERR_TIMEOUT;
        }
    }
}

esp_err_t Provisioning::read_frame(uint8_t &type, std::string &payload, TickType_t first_byte_timeout)
{
    uint8_t byte = 0;
    do {
        if (!read_exact(&byte, 1, first_byte_timeout)) return ESP_ERR_TIMEOUT;
    } while (byte != 0xa5);
    if (!read_exact(&byte, 1, pdMS_TO_TICKS(500)) || byte != 0x5a) return ESP_ERR_INVALID_CRC;

    uint8_t header[4];
    if (!read_exact(header, sizeof(header), pdMS_TO_TICKS(1000))) return ESP_ERR_TIMEOUT;
    if (header[0] != config::kProtocolVersion) return ESP_ERR_INVALID_VERSION;
    type = header[1];
    const uint16_t length = static_cast<uint16_t>(header[2]) | (static_cast<uint16_t>(header[3]) << 8);
    if (length > kMaxPayload) return ESP_ERR_INVALID_SIZE;
    payload.resize(length);
    if (length && !read_exact(reinterpret_cast<uint8_t *>(payload.data()), length, pdMS_TO_TICKS(1500))) {
        return ESP_ERR_TIMEOUT;
    }
    uint8_t crc_bytes[2];
    if (!read_exact(crc_bytes, 2, pdMS_TO_TICKS(500))) return ESP_ERR_TIMEOUT;
    const uint16_t received = static_cast<uint16_t>(crc_bytes[0]) | (static_cast<uint16_t>(crc_bytes[1]) << 8);
    const uint16_t expected = crc16_frame(type, length, reinterpret_cast<const uint8_t *>(payload.data()));
    return received == expected ? ESP_OK : ESP_ERR_INVALID_CRC;
}

esp_err_t Provisioning::send_frame(uint8_t type, const std::string &payload)
{
    if (payload.size() > kMaxPayload) return ESP_ERR_INVALID_SIZE;
    const uint16_t length = payload.size();
    uint8_t header[6] = {0xa5, 0x5a, config::kProtocolVersion, type,
                         static_cast<uint8_t>(length & 0xff), static_cast<uint8_t>(length >> 8)};
    const uint16_t crc = crc16_frame(type, length, reinterpret_cast<const uint8_t *>(payload.data()));
    uint8_t crc_bytes[2] = {static_cast<uint8_t>(crc & 0xff), static_cast<uint8_t>(crc >> 8)};
    if (uart_write_bytes(kUart, header, sizeof(header)) != sizeof(header) ||
        (length && uart_write_bytes(kUart, payload.data(), length) != length) ||
        uart_write_bytes(kUart, crc_bytes, sizeof(crc_bytes)) != sizeof(crc_bytes)) {
        return ESP_FAIL;
    }
    return uart_wait_tx_done(kUart, pdMS_TO_TICKS(1000));
}

esp_err_t Provisioning::send_info()
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;
    const std::string id = DeviceConfigStore::make_device_id();
    cJSON_AddStringToObject(root, "device_id", id.c_str());
    cJSON_AddStringToObject(root, "fw_version", config::kFirmwareVersion);
    cJSON_AddNumberToObject(root, "protocol", config::kProtocolVersion);
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!json) return ESP_ERR_NO_MEM;
    esp_err_t err = send_frame(2, json);
    cJSON_free(json);
    return err;
}

esp_err_t Provisioning::send_nack(const char *reason)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;
    cJSON_AddBoolToObject(root, "ok", false);
    cJSON_AddStringToObject(root, "error", reason);
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!json) return ESP_ERR_NO_MEM;
    esp_err_t err = send_frame(5, json);
    cJSON_free(json);
    return err;
}

bool Provisioning::parse_set_config(const std::string &payload, PendingConfig &pending)
{
    cJSON *root = cJSON_ParseWithLength(payload.data(), payload.size());
    if (!root) return false;
    const cJSON *ssid = cJSON_GetObjectItemCaseSensitive(root, "wifi_ssid");
    const cJSON *password = cJSON_GetObjectItemCaseSensitive(root, "wifi_password");
    const cJSON *server = cJSON_GetObjectItemCaseSensitive(root, "server_url");
    const cJSON *token = cJSON_GetObjectItemCaseSensitive(root, "pairing_token");
    const bool valid = cJSON_IsString(ssid) && cJSON_IsString(password) && cJSON_IsString(server) && cJSON_IsString(token) &&
                       ssid->valuestring && password->valuestring && server->valuestring && token->valuestring &&
                       std::strlen(ssid->valuestring) > 0 && std::strlen(ssid->valuestring) <= 32 &&
                       std::strlen(password->valuestring) <= 64 && std::strlen(token->valuestring) > 0 &&
                       valid_server_url(server->valuestring);
    if (valid) {
        pending.config.wifi_ssid = ssid->valuestring;
        pending.config.wifi_password = password->valuestring;
        pending.config.server_url = normalized_server_url(server->valuestring);
        pending.pairing_token = token->valuestring;
    }
    cJSON_Delete(root);
    return valid;
}

esp_err_t Provisioning::claim(DeviceConfig &config, const std::string &pairing_token)
{
    cJSON *request = cJSON_CreateObject();
    if (!request) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(request, "device_id", config.device_id.c_str());
    cJSON_AddStringToObject(request, "pairing_token", pairing_token.c_str());
    char *body = cJSON_PrintUnformatted(request);
    cJSON_Delete(request);
    if (!body) return ESP_ERR_NO_MEM;

    HttpResponse response;
    const std::string url = http_endpoint(config.server_url, "/api/v1/devices/claim");
    esp_http_client_config_t http_config = {};
    http_config.url = url.c_str();
    http_config.method = HTTP_METHOD_POST;
    http_config.event_handler = http_data_handler;
    http_config.user_data = &response;
    http_config.timeout_ms = 12000;
    http_config.buffer_size = 2048;
    http_config.buffer_size_tx = 2048;
    http_config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    if (!client) {
        cJSON_free(body);
        return ESP_ERR_NO_MEM;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, body, std::strlen(body));
    esp_err_t err = esp_http_client_perform(client);
    const int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    cJSON_free(body);
    if (err != ESP_OK || status != 200 || response.overflow) {
        ESP_LOGW(kTag, "device claim failed, HTTP status=%d, err=%s", status, esp_err_to_name(err));
        return ESP_FAIL;
    }

    cJSON *root = cJSON_ParseWithLength(response.body.data(), response.body.size());
    if (!root) return ESP_FAIL;
    const cJSON *token = cJSON_GetObjectItemCaseSensitive(root, "device_token");
    if (cJSON_IsString(token) && token->valuestring && token->valuestring[0]) {
        config.device_token = token->valuestring;
    }
    cJSON_Delete(root);
    if (!config.valid()) return ESP_FAIL;
    ESP_LOGI(kTag, "device claimed successfully");
    return ESP_OK;
}

esp_err_t Provisioning::authorize_network(const DeviceConfig &config, const std::string &pairing_token)
{
    if (!config.valid()) return ESP_ERR_INVALID_ARG;
    cJSON *request = cJSON_CreateObject();
    if (!request) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(request, "device_id", config.device_id.c_str());
    cJSON_AddStringToObject(request, "pairing_token", pairing_token.c_str());
    cJSON_AddStringToObject(request, "device_token", config.device_token.c_str());
    char *body = cJSON_PrintUnformatted(request);
    cJSON_Delete(request);
    if (!body) return ESP_ERR_NO_MEM;

    HttpResponse response;
    const std::string url = http_endpoint(config.server_url, "/api/v1/devices/network_authorize");
    esp_http_client_config_t http_config = {};
    http_config.url = url.c_str();
    http_config.method = HTTP_METHOD_POST;
    http_config.event_handler = http_data_handler;
    http_config.user_data = &response;
    http_config.timeout_ms = 12000;
    http_config.buffer_size = 2048;
    http_config.buffer_size_tx = 2048;
    http_config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    if (!client) {
        cJSON_free(body);
        return ESP_ERR_NO_MEM;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, body, std::strlen(body));
    esp_err_t err = esp_http_client_perform(client);
    const int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    cJSON_free(body);
    if (err != ESP_OK || status != 200 || response.overflow) {
        ESP_LOGW(kTag, "network authorization failed, HTTP status=%d, err=%s", status, esp_err_to_name(err));
        return ESP_FAIL;
    }

    cJSON *root = cJSON_ParseWithLength(response.body.data(), response.body.size());
    if (!root) return ESP_FAIL;
    const bool ok = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, "ok"));
    cJSON_Delete(root);
    if (!ok) return ESP_FAIL;
    ESP_LOGI(kTag, "network change authorized without rotating device identity");
    return ESP_OK;
}
