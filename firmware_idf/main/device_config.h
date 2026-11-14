#pragma once

#include <cstdint>
#include <string>
#include "esp_err.h"

struct DeviceConfig {
    std::string wifi_ssid;
    std::string wifi_password;
    std::string server_url;
    std::string device_id;
    std::string device_token;
    uint32_t config_version = 0;

    bool valid() const;
};

class DeviceConfigStore {
public:
    esp_err_t init();
    esp_err_t load(DeviceConfig &out) const;
    esp_err_t save(DeviceConfig &config);
    static std::string make_device_id();
};

/** 设备运行期偏好的 NVS 存储（音量、自定义画面等，与配网信息同一个命名空间）。 */
class DevicePrefs {
public:
    static uint8_t get_u8(const char *key, uint8_t fallback);
    static esp_err_t set_u8(const char *key, uint8_t value);
    static std::string get_str(const char *key);
    static esp_err_t set_str(const char *key, const std::string &value);
    static esp_err_t erase(const char *key);
};

std::string normalized_server_url(const std::string &url);
std::string websocket_url(const DeviceConfig &config);
std::string http_endpoint(const std::string &server_url, const std::string &path);
