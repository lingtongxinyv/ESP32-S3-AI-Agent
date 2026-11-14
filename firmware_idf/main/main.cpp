#include "app_config.h"
#include "app_control.h"
#include "audio_engine.h"
#include "device_config.h"
#include "display.h"
#include "esp_err.h"
#include "esp_log.h"
#include "network.h"
#include "ota_manager.h"
#include "wake_word_detector.h"

extern "C" void app_main()
{
    ESP_LOGI("main", "ESP32-S3 AI Agent firmware %s, protocol %u",
             config::kFirmwareVersion, config::kProtocolVersion);

    static DeviceConfigStore store;
    ESP_ERROR_CHECK(store.init());

    static Display display;
    esp_err_t display_err = display.init();
    if (display_err != ESP_OK) {
        ESP_LOGW("main", "OLED unavailable: %s", esp_err_to_name(display_err));
    }

    static WakeWordDetector wake;
    const esp_err_t wake_err = wake.init();
    if (wake_err != ESP_OK) {
        ESP_LOGW("main", "offline wake unavailable: %s; BOOT button remains available", esp_err_to_name(wake_err));
    }

    static AudioEngine audio(wake);
    ESP_ERROR_CHECK(audio.init());
    static Network network(audio, wake);
    static OtaManager ota;
    ESP_ERROR_CHECK(ota.init());
    static app::Control control(store, audio, display, network, ota);
    ESP_ERROR_CHECK(control.start());
}
