#pragma once

#include <cstdint>
#include "ble_provisioning.h"
#include "device_config.h"
#include "esp_err.h"
#include "provisioning.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

class AudioEngine;
class DeviceConfigStore;
class Display;
class Network;
class OtaManager;

namespace app {

enum class State : uint8_t {
    UNPROVISIONED,
    CONNECTING,
    IDLE,
    LISTENING,
    THINKING,
    SPEAKING,
    ERROR,
    OTA,
};

enum class EventType : uint8_t {
    WifiConnected,
    WifiDisconnected,
    WsConnected,
    WsDisconnected,
    AudioFrame,
    PlaybackFinished,
    AudioError,
    AsrResult,
    LlmDelta,
    AudioStart,
    AudioEnd,
    Done,
    ServerError,
    Panel,
    PanelAuto,
    OtaCheck,
    WakeWordDetected,
};

struct Event {
    EventType type;
    uint32_t session;
    uint32_t value;
    char text[192];
};

const char *state_name(State state);
State state();
uint32_t session();
bool post_event(EventType type, uint32_t session = 0, uint32_t value = 0, const char *text = nullptr);

class Control {
public:
    Control(DeviceConfigStore &store, AudioEngine &audio, Display &display, Network &network, OtaManager &ota);
    esp_err_t start();

private:
    static void task_entry(void *arg);
    void run();
    void handle_event(const Event &event);
    void handle_button(uint32_t now_ms);
    void enter_provisioning();
    void begin_listening();
    void finish_listening();
    void abort_session();
    void set_state(State next);
    void set_error(const char *message);
    void clear_auto_face(bool redraw);
    bool session_matches(uint32_t value) const;

    DeviceConfigStore &store_;
    AudioEngine &audio_;
    Display &display_;
    Network &network_;
    OtaManager &ota_;
    Provisioning provisioning_;
    BleProvisioning ble_;
    DeviceConfig config_;
    QueueHandle_t events_ = nullptr;
    State state_ = State::UNPROVISIONED;
    char text_[192] = {};
    bool network_initialized_ = false;
    uint32_t session_ = 0;
    uint32_t recording_ms_ = 0;
    uint32_t listening_started_ms_ = 0;
    uint32_t speech_ms_ = 0;
    uint32_t silence_ms_ = 0;
    uint64_t energy_total_ = 0;
    uint32_t energy_frames_ = 0;
    uint32_t peak_energy_ = 0;
    uint32_t speaking_started_ms_ = 0;
    uint32_t next_reconnect_ms_ = 0;
    uint32_t next_ping_ms_ = 0;
    uint32_t next_ota_check_ms_ = 0;
    bool first_ws_connected_ = false;  // 首次 WsConnected 时才排 60s 首次自检，之后由 OTA 分支推到 6h
    uint32_t error_until_ms_ = 0;
    bool auto_face_active_ = false;
    uint32_t auto_face_session_ = 0;
    uint32_t auto_face_restore_ms_ = 0;
    uint32_t auto_face_watchdog_ms_ = 0;
    uint32_t auto_face_blocked_session_ = 0;
    bool wifi_connected_ = false;
    bool provisioned_ = false;
    bool force_provisioning_ = false;
    bool end_pending_ = false;
    bool button_stable_ = true;
    bool button_last_raw_ = true;
    uint32_t button_changed_ms_ = 0;
    uint32_t button_pressed_ms_ = 0;
    bool long_press_handled_ = false;
};

} // namespace app
