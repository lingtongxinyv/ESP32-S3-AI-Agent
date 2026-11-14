#pragma once

#include <cstdint>
#include "device_config.h"
#include "esp_err.h"
#include "esp_ota_ops.h"

class OtaManager {
public:
    esp_err_t init();
    void confirm_running();
    bool self_check_expired() const;
    esp_err_t check_and_update(const DeviceConfig &config);

private:
    bool pending_verify_ = false;
    uint32_t boot_ms_ = 0;
};
