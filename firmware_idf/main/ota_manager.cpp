#include "ota_manager.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <string>
#include "app_config.h"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "psa/crypto.h"

namespace {
constexpr char kTag[] = "ota";
constexpr uint32_t kSelfCheckTimeoutMs = 120000;

struct Manifest {
    int firmware_id = 0;
    std::string version;
    std::string sha256;
    std::string download_url;
    size_t size = 0;
};

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

bool parse_hex_sha(const std::string &text, uint8_t output[32])
{
    if (text.size() != 64) return false;
    auto nibble = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (size_t i = 0; i < 32; ++i) {
        const int high = nibble(text[i * 2]);
        const int low = nibble(text[i * 2 + 1]);
        if (high < 0 || low < 0) return false;
        output[i] = static_cast<uint8_t>((high << 4) | low);
    }
    return true;
}

bool compatible_hardware(const char *hardware)
{
    if (!hardware) return false;
    std::string value(hardware);
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) { return std::tolower(c); });
    return value == "esp32s3" || value == "esp32-s3";
}

bool compatible_protocol(const cJSON *root)
{
    const cJSON *protocol = cJSON_GetObjectItemCaseSensitive(root, "protocol");
    if (!protocol) protocol = cJSON_GetObjectItemCaseSensitive(root, "protocol_version");
    if (cJSON_IsNumber(protocol)) return protocol->valueint == config::kProtocolVersion;
    if (cJSON_IsString(protocol) && protocol->valuestring) {
        return std::strcmp(protocol->valuestring, "1") == 0;
    }
    return false;
}

esp_err_t fetch_manifest(const DeviceConfig &config, Manifest &manifest, bool &update_available)
{
    HttpResponse response;
    // 令牌走 Authorization 头，不进 URL。
    const std::string url = http_endpoint(config.server_url,
        std::string("/api/v1/device/ota/current?current_version=") + config::kFirmwareVersion);
    esp_http_client_config_t http_config = {};
    http_config.url = url.c_str();
    http_config.method = HTTP_METHOD_GET;
    http_config.event_handler = http_data_handler;
    http_config.user_data = &response;
    http_config.timeout_ms = 12000;
    http_config.buffer_size = 2048;
    http_config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    if (!client) return ESP_ERR_NO_MEM;
    const std::string auth = "Bearer " + config.device_token;
    esp_http_client_set_header(client, "Authorization", auth.c_str());
    esp_err_t err = esp_http_client_perform(client);
    const int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK || status != 200 || response.overflow) {
        ESP_LOGW(kTag, "manifest request failed, HTTP status=%d", status);
        return ESP_FAIL;
    }

    cJSON *root = cJSON_ParseWithLength(response.body.data(), response.body.size());
    if (!root) return ESP_FAIL;
    const cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "version");
    const cJSON *size = cJSON_GetObjectItemCaseSensitive(root, "size");
    const cJSON *sha = cJSON_GetObjectItemCaseSensitive(root, "sha256");
    const cJSON *firmware_id = cJSON_GetObjectItemCaseSensitive(root, "firmware_id");
    const cJSON *url_item = cJSON_GetObjectItemCaseSensitive(root, "download_url");
    if (!cJSON_IsString(url_item)) url_item = cJSON_GetObjectItemCaseSensitive(root, "url");
    const cJSON *hardware = cJSON_GetObjectItemCaseSensitive(root, "hardware");
    update_available = cJSON_IsString(version) && version->valuestring &&
                       std::strcmp(version->valuestring, config::kFirmwareVersion) != 0;
    bool valid = update_available && cJSON_IsNumber(size) && size->valuedouble > 0 &&
                 cJSON_IsNumber(firmware_id) && firmware_id->valueint > 0 &&
                 cJSON_IsString(sha) && sha->valuestring && cJSON_IsString(url_item) && url_item->valuestring &&
                 cJSON_IsString(hardware) && compatible_hardware(hardware->valuestring) && compatible_protocol(root);
    if (valid) {
        manifest.firmware_id = firmware_id->valueint;
        manifest.version = version->valuestring;
        manifest.size = static_cast<size_t>(size->valuedouble);
        manifest.sha256 = sha->valuestring;
        manifest.download_url = url_item->valuestring;
        uint8_t decoded[32];
        valid = parse_hex_sha(manifest.sha256, decoded) &&
                (manifest.download_url.rfind("https://", 0) == 0 || manifest.download_url.rfind("http://", 0) == 0);
    }
    cJSON_Delete(root);
    return !update_available || valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

esp_err_t report_result(const DeviceConfig &config, const Manifest &manifest, const char *status, const char *detail)
{
    if (manifest.firmware_id <= 0) return ESP_ERR_INVALID_ARG;
    const std::string url = http_endpoint(config.server_url, "/api/v1/device/ota/result");
    char body[256];
    std::snprintf(body, sizeof(body),
                  "{\"firmware_id\":%d,\"status\":\"%s\",\"detail\":\"%s\"}",
                  manifest.firmware_id, status, detail);
    esp_http_client_config_t http_config = {};
    http_config.url = url.c_str();
    http_config.method = HTTP_METHOD_POST;
    http_config.timeout_ms = 8000;
    http_config.buffer_size = 1024;
    http_config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_http_client_handle_t client = esp_http_client_init(&http_config);
    if (!client) return ESP_ERR_NO_MEM;
    esp_http_client_set_header(client, "Content-Type", "application/json");
    const std::string auth = "Bearer " + config.device_token;
    esp_http_client_set_header(client, "Authorization", auth.c_str());
    esp_http_client_set_post_field(client, body, static_cast<int>(std::strlen(body)));
    const esp_err_t err = esp_http_client_perform(client);
    const int status_code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK || status_code / 100 != 2) {
        ESP_LOGW(kTag, "OTA result report failed, err=%d status=%d", err, status_code);
    }
    return err;
}

esp_err_t verify_partition_sha(const esp_partition_t *partition, size_t size, const std::string &expected_hex)
{
    uint8_t expected[32];
    if (!parse_hex_sha(expected_hex, expected) || !partition || size > partition->size) return ESP_ERR_INVALID_ARG;
    if (psa_crypto_init() != PSA_SUCCESS) return ESP_FAIL;
    psa_hash_operation_t operation = PSA_HASH_OPERATION_INIT;
    if (psa_hash_setup(&operation, PSA_ALG_SHA_256) != PSA_SUCCESS) return ESP_FAIL;
    uint8_t buffer[2048];
    size_t offset = 0;
    while (offset < size) {
        const size_t count = std::min(sizeof(buffer), size - offset);
        if (esp_partition_read(partition, offset, buffer, count) != ESP_OK ||
            psa_hash_update(&operation, buffer, count) != PSA_SUCCESS) {
            psa_hash_abort(&operation);
            return ESP_FAIL;
        }
        offset += count;
    }
    uint8_t actual[32];
    size_t actual_size = 0;
    const psa_status_t status = psa_hash_finish(&operation, actual, sizeof(actual), &actual_size);
    if (status != PSA_SUCCESS || actual_size != sizeof(actual) || std::memcmp(actual, expected, sizeof(actual)) != 0) {
        ESP_LOGE(kTag, "downloaded image SHA256 mismatch");
        return ESP_ERR_INVALID_CRC;
    }
    return ESP_OK;
}
} // namespace

esp_err_t OtaManager::init()
{
    boot_ms_ = esp_timer_get_time() / 1000;
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (running && esp_ota_get_state_partition(running, &state) == ESP_OK) {
        pending_verify_ = state == ESP_OTA_IMG_PENDING_VERIFY;
    }
    return ESP_OK;
}

void OtaManager::confirm_running()
{
    if (!pending_verify_) return;
    if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
        pending_verify_ = false;
        ESP_LOGI(kTag, "new firmware self-check passed; rollback cancelled");
    }
}

bool OtaManager::self_check_expired() const
{
    return pending_verify_ && static_cast<uint32_t>(esp_timer_get_time() / 1000) - boot_ms_ > kSelfCheckTimeoutMs;
}

esp_err_t OtaManager::check_and_update(const DeviceConfig &config)
{
    Manifest manifest;
    bool update_available = false;
    esp_err_t err = fetch_manifest(config, manifest, update_available);
    if (err != ESP_OK || !update_available) return err;

    const esp_partition_t *running = esp_ota_get_running_partition();
    const esp_partition_t *target = esp_ota_get_next_update_partition(running);
    if (!running || !target || manifest.size > target->size) return ESP_ERR_INVALID_SIZE;
    ESP_LOGI(kTag, "downloading version %s (%lu bytes) into inactive OTA slot",
             manifest.version.c_str(), static_cast<unsigned long>(manifest.size));

    esp_http_client_config_t http_config = {};
    http_config.url = manifest.download_url.c_str();
    http_config.timeout_ms = 20000;
    http_config.buffer_size = 4096;
    http_config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_https_ota_config_t ota_config = {};
    ota_config.http_config = &http_config;
    ota_config.bulk_flash_erase = false;
    esp_https_ota_handle_t handle = nullptr;
    err = esp_https_ota_begin(&ota_config, &handle);
    if (err != ESP_OK) return err;

    esp_app_desc_t description = {};
    err = esp_https_ota_get_img_desc(handle, &description);
    if (err == ESP_OK && std::strcmp(description.version, manifest.version.c_str()) != 0) {
        err = ESP_ERR_INVALID_VERSION;
    }
    if (err == ESP_OK) {
        const int image_size = esp_https_ota_get_image_size(handle);
        if (image_size > 0 && static_cast<size_t>(image_size) != manifest.size) err = ESP_ERR_INVALID_SIZE;
    }
    while (true) {
        err = esp_https_ota_perform(handle);
        if (err != ESP_ERR_HTTPS_OTA_IN_PROGRESS) break;
    }
    // 最后一次 0 字节读取才会切换到 SUCCESS；提前退出会让 finish 跳过启动槽切换。
    if (err != ESP_OK || !esp_https_ota_is_complete_data_received(handle)) {
        esp_https_ota_abort(handle);
        return err == ESP_OK ? ESP_FAIL : err;
    }
    err = esp_https_ota_finish(handle);
    if (err != ESP_OK) return err;

    err = verify_partition_sha(target, manifest.size, manifest.sha256);
    if (err != ESP_OK) {
        report_result(config, manifest, "failed", "sha256 mismatch");
        esp_ota_set_boot_partition(running);
        return err;
    }
    report_result(config, manifest, "success", "downloaded and verified");
    ESP_LOGI(kTag, "OTA image verified; restarting");
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
    return ESP_OK;
}
