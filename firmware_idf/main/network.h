#pragma once

#include <atomic>
#include <cstdint>
#include <string>
#include "audio_engine.h"
#include "cJSON.h"
#include "device_config.h"
#include "esp_err.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

class WakeWordDetector;

class Network {
public:
    Network(AudioEngine &audio, WakeWordDetector &wake) : audio_(audio), wake_(wake) {}

    esp_err_t init(const DeviceConfig &config);
    esp_err_t update_config(const DeviceConfig &config);
    /** 绑定成功后刷新设备令牌，下一次建连即携带 token。 */
    void set_device_token(const std::string &token);
    esp_err_t reconnect_wifi();
    int last_disconnect_reason() const { return last_disconnect_reason_.load(); }
    esp_err_t last_wifi_error() const { return last_wifi_error_.load(); }
    void start_sntp();
    bool time_synchronized() const;
    esp_err_t start_websocket();
    void stop_websocket();
    bool websocket_connected() const { return ws_connected_.load(); }
    bool wifi_connected() const { return wifi_connected_.load(); }

    bool send_hello();
    bool send_session_message(const char *type, uint32_t session);
    bool send_start(uint32_t session);  // start 消息需带 sample_rate（服务端 handle_start 校验）
    bool send_ping(uint32_t session);
    bool send_panel_ack(bool ok, int volume, const char *error = nullptr);
    bool send_config_ack(uint32_t version, bool ok, const char *error = nullptr);

private:
    static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data);
    static void websocket_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data);
    static void tx_task_entry(void *arg);
    void tx_task();
    void handle_websocket_data(const esp_websocket_event_data_t &event);
    void handle_json(const char *data, size_t length);
    void handle_binary(const uint8_t *data, size_t length);
    bool send_json(cJSON *root);
    bool send_text(const char *data, size_t length);

    AudioEngine &audio_;
    WakeWordDetector &wake_;
    DeviceConfig config_;
    esp_netif_t *netif_ = nullptr;
    esp_websocket_client_handle_t ws_ = nullptr;
    SemaphoreHandle_t ws_mutex_ = nullptr;
    std::atomic<bool> wifi_connected_{false};
    std::atomic<bool> ws_connected_{false};
    std::atomic<bool> stopping_ws_{false};
    std::atomic<int> last_disconnect_reason_{0};
    std::atomic<esp_err_t> last_wifi_error_{ESP_OK};
    bool wifi_initialized_ = false;
    bool sntp_initialized_ = false;
    std::string ws_uri_;
    std::string ws_subprotocol_;  // "bearer.<token>"，生命周期与 ws_ 一致
    std::string rx_message_;
    int rx_expected_ = 0;
    uint8_t rx_opcode_ = 0;
};
