#pragma once

#include <cstdint>
#include <string>
#include "device_config.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"

struct PendingConfig {
    DeviceConfig config;
    std::string pairing_token;
};

class Provisioning {
public:
    esp_err_t begin();
    /** 配网结束后恢复日志输出（UART0 不再被配网帧占用）。 */
    void restore_logs();
    /** 非阻塞读取一帧串口配网请求；timeout_ms 内没有完整配置则返回 ESP_ERR_TIMEOUT。 */
    esp_err_t try_receive(PendingConfig &pending, uint32_t timeout_ms);
    esp_err_t claim(DeviceConfig &config, const std::string &pairing_token);
    /** 已绑定设备改网授权：验证一次性授权和原设备令牌，不轮换设备身份。 */
    esp_err_t authorize_network(const DeviceConfig &config, const std::string &pairing_token);

private:
    esp_err_t read_frame(uint8_t &type, std::string &payload, TickType_t first_byte_timeout);
    esp_err_t send_frame(uint8_t type, const std::string &payload);
    esp_err_t send_info();
    esp_err_t send_nack(const char *reason);
    bool parse_set_config(const std::string &payload, PendingConfig &pending);
};
