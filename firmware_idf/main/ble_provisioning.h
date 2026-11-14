#pragma once

#include <string>
#include "device_config.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

struct BlePendingConfig {
    DeviceConfig config;
    std::string pairing_token;
};

/**
 * BLE 配网服务：手机连上后通过自定义 GATT 下发 Wi-Fi、服务器地址和一次性配对令牌。
 * 回调只把结果塞进队列，由控制任务轮询，避免在 NimBLE 上下文里做网络操作。
 */
class BleProvisioning {
public:
    esp_err_t start(const std::string &device_id);
    void stop();
    bool take_pending(BlePendingConfig &out);
    void notify_state(const char *state, const char *message = nullptr);
};
