#include "device_config.h"

#include <cctype>
#include <cstdio>
#include "esp_check.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "nvs.h"
#include "nvs_flash.h"

namespace {
constexpr char kTag[] = "config";
constexpr char kNamespace[] = "agent";
constexpr char kVersionKey[] = "cfg_version";

esp_err_t read_string(nvs_handle_t handle, const char *key, std::string &out)
{
    size_t size = 0;
    esp_err_t err = nvs_get_str(handle, key, nullptr, &size);
    if (err != ESP_OK || size == 0) {
        return err == ESP_OK ? ESP_ERR_NOT_FOUND : err;
    }
    out.resize(size - 1);
    err = nvs_get_str(handle, key, out.data(), &size);
    if (err != ESP_OK) {
        out.clear();
    }
    return err;
}

esp_err_t write_string(nvs_handle_t handle, const char *key, const std::string &value)
{
    return nvs_set_str(handle, key, value.c_str());
}

bool supported_scheme(const std::string &url)
{
    return url.rfind("https://", 0) == 0 || url.rfind("http://", 0) == 0 ||
           url.rfind("wss://", 0) == 0 || url.rfind("ws://", 0) == 0;
}
} // namespace

bool DeviceConfig::valid() const
{
    return !wifi_ssid.empty() && wifi_ssid.size() <= 32 && wifi_password.size() <= 64 &&
           !server_url.empty() && supported_scheme(server_url) &&
           server_url.find_first_of("?#") == std::string::npos &&
           !device_id.empty() && !device_token.empty();
}

esp_err_t DeviceConfigStore::init()
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), kTag, "erase NVS");
        err = nvs_flash_init();
    }
    return err;
}

esp_err_t DeviceConfigStore::load(DeviceConfig &out) const
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(kNamespace, NVS_READONLY, &handle);
    if (err != ESP_OK) {
        return err;
    }
    out = DeviceConfig{};
    out.device_id = make_device_id();
    read_string(handle, "wifi_ssid", out.wifi_ssid);
    read_string(handle, "wifi_pass", out.wifi_password);
    read_string(handle, "server_url", out.server_url);
    read_string(handle, "device_id", out.device_id);
    read_string(handle, "device_token", out.device_token);
    uint32_t version = 0;
    if (nvs_get_u32(handle, kVersionKey, &version) == ESP_OK) {
        out.config_version = version;
    }
    nvs_close(handle);
    out.server_url = normalized_server_url(out.server_url);
    if (out.device_id.empty()) {
        out.device_id = make_device_id();
    }
    return out.valid() ? ESP_OK : ESP_ERR_NOT_FOUND;
}

esp_err_t DeviceConfigStore::save(DeviceConfig &config)
{
    config.server_url = normalized_server_url(config.server_url);
    if (config.device_id.empty()) {
        config.device_id = make_device_id();
    }
    nvs_handle_t handle;
    esp_err_t err = nvs_open(kNamespace, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        return err;
    }
    ++config.config_version;
    err = write_string(handle, "wifi_ssid", config.wifi_ssid);
    if (err == ESP_OK) err = write_string(handle, "wifi_pass", config.wifi_password);
    if (err == ESP_OK) err = write_string(handle, "server_url", config.server_url);
    if (err == ESP_OK) err = write_string(handle, "device_id", config.device_id);
    if (err == ESP_OK) err = write_string(handle, "device_token", config.device_token);
    if (err == ESP_OK) err = nvs_set_u32(handle, kVersionKey, config.config_version);
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    if (err == ESP_OK) {
        ESP_LOGI(kTag, "configuration saved, version=%lu", static_cast<unsigned long>(config.config_version));
    }
    return err;
}

std::string DeviceConfigStore::make_device_id()
{
    uint8_t mac[6] = {};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char id[32];
    std::snprintf(id, sizeof(id), "esp32s3-%02x%02x%02x%02x%02x%02x",
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    return id;
}

uint8_t DevicePrefs::get_u8(const char *key, uint8_t fallback)
{
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READONLY, &handle) != ESP_OK) return fallback;
    uint8_t value = fallback;
    if (nvs_get_u8(handle, key, &value) != ESP_OK) value = fallback;
    nvs_close(handle);
    return value;
}

esp_err_t DevicePrefs::set_u8(const char *key, uint8_t value)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(kNamespace, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    err = nvs_set_u8(handle, key, value);
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    return err;
}

std::string DevicePrefs::get_str(const char *key)
{
    nvs_handle_t handle;
    if (nvs_open(kNamespace, NVS_READONLY, &handle) != ESP_OK) return {};
    std::string value;
    read_string(handle, key, value);
    nvs_close(handle);
    return value;
}

esp_err_t DevicePrefs::set_str(const char *key, const std::string &value)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(kNamespace, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    err = value.empty() ? nvs_erase_key(handle, key) : write_string(handle, key, value);
    if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    return err;
}

esp_err_t DevicePrefs::erase(const char *key)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(kNamespace, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    err = nvs_erase_key(handle, key);
    if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
    if (err == ESP_OK) err = nvs_commit(handle);
    nvs_close(handle);
    return err;
}

std::string normalized_server_url(const std::string &url)
{
    std::string result = url;
    while (!result.empty() && (result.back() == '/' || std::isspace(static_cast<unsigned char>(result.back())))) {
        result.pop_back();
    }
    return result;
}

std::string websocket_url(const DeviceConfig &config)
{
    std::string base = normalized_server_url(config.server_url);
    if (base.rfind("https://", 0) == 0) {
        base.replace(0, 5, "wss");
    } else if (base.rfind("http://", 0) == 0) {
        base.replace(0, 4, "ws");
    }
    // cpolar 在 WS Upgrade 时剥 Authorization 头，但透传 Sec-WebSocket-Protocol（实测）。
    // 令牌通过 Sec-WebSocket-Protocol: bearer.<token> 传递（见 network.cpp）。
    base += "/ws/v1/device";
    return base;
}

std::string http_endpoint(const std::string &server_url, const std::string &path)
{
    std::string base = normalized_server_url(server_url);
    if (base.rfind("wss://", 0) == 0) {
        base.replace(0, 3, "https");
    } else if (base.rfind("ws://", 0) == 0) {
        base.replace(0, 2, "http");
    }
    base += path;
    return base;
}
