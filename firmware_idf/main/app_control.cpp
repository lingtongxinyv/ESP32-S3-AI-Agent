#include "app_control.h"

#include <atomic>
#include <cstdio>
#include <cstring>
#include <string>
#include "audio_engine.h"
#include "display.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "network.h"
#include "ota_manager.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "control";
std::atomic<app::State> g_state{app::State::UNPROVISIONED};
std::atomic<uint32_t> g_session{0};
QueueHandle_t g_events = nullptr;

uint32_t now_ms()
{
    return static_cast<uint32_t>(esp_timer_get_time() / 1000);
}

bool elapsed(uint32_t now, uint32_t deadline)
{
    return static_cast<int32_t>(now - deadline) >= 0;
}

void copy_text(char *destination, size_t size, const char *source)
{
    if (!destination || size == 0) return;
    std::snprintf(destination, size, "%s", source ? source : "");
}

void log_memory(const char *stage)
{
    constexpr uint32_t kInternal = MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL;
    ESP_LOGI(kTag, "memory %s: internal_free=%u internal_largest=%u psram_free=%u",
             stage,
             static_cast<unsigned>(heap_caps_get_free_size(kInternal)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(kInternal)),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM)));
}
} // namespace

namespace app {

const char *state_name(State state)
{
    switch (state) {
        case State::UNPROVISIONED: return "UNPROVISIONED";
        case State::CONNECTING: return "CONNECTING";
        case State::IDLE: return "IDLE";
        case State::LISTENING: return "LISTENING";
        case State::THINKING: return "THINKING";
        case State::SPEAKING: return "SPEAKING";
        case State::ERROR: return "ERROR";
        case State::OTA: return "OTA";
    }
    return "UNKNOWN";
}

State state()
{
    return g_state.load();
}

uint32_t session()
{
    return g_session.load();
}

bool post_event(EventType type, uint32_t session, uint32_t value, const char *text)
{
    if (!g_events) return false;
    Event event = {};
    event.type = type;
    event.session = session;
    event.value = value;
    copy_text(event.text, sizeof(event.text), text);
    return xQueueSend(g_events, &event, 0) == pdPASS;
}

Control::Control(DeviceConfigStore &store, AudioEngine &audio, Display &display, Network &network, OtaManager &ota)
    : store_(store), audio_(audio), display_(display), network_(network), ota_(ota)
{
}

esp_err_t Control::start()
{
    events_ = xQueueCreate(40, sizeof(Event));
    if (!events_) return ESP_ERR_NO_MEM;
    g_events = events_;

    // BOOT 键：GPIO0 对 GND（原理图确认），内部上拉，按下为低电平
    gpio_config_t button = {};
    button.pin_bit_mask = 1ULL << config::kButtonGpio;
    button.mode = GPIO_MODE_INPUT;
    button.pull_up_en = GPIO_PULLUP_ENABLE;
    button.pull_down_en = GPIO_PULLDOWN_DISABLE;
    button.intr_type = GPIO_INTR_DISABLE;
    esp_err_t err = gpio_config(&button);
    if (err != ESP_OK) return err;

    if (xTaskCreate(task_entry, "control", 12288, this, 4, nullptr) != pdPASS) return ESP_ERR_NO_MEM;
    return ESP_OK;
}

void Control::task_entry(void *arg)
{
    static_cast<Control *>(arg)->run();
}

void Control::run()
{
    // 恢复手机端上次下发的音量与自定义画面，掉电重启后仍然生效。
    audio_.set_volume(DevicePrefs::get_u8("volume", 100));
    const std::string saved_face = DevicePrefs::get_str("face_emotion");
    if (!saved_face.empty()) {
        display_.show_face(saved_face.c_str(), DevicePrefs::get_str("face_text").c_str());
    }
    provisioned_ = store_.load(config_) == ESP_OK;
    if (provisioned_) {
        set_state(State::CONNECTING);
        const esp_err_t net_err = network_.init(config_);
        if (net_err == ESP_OK) {
            network_initialized_ = true;
        } else {
            ESP_LOGE(kTag, "network init failed: %s", esp_err_to_name(net_err));
            set_error("WIFI INIT FAILED");
        }
    } else {
        enter_provisioning();
    }
    next_ping_ms_ = now_ms() + 20000;
    next_ota_check_ms_ = now_ms() + 60000;

    uint32_t last_heartbeat_ms = 0;
    for (;;) {
        Event event;
        if (xQueueReceive(events_, &event, pdMS_TO_TICKS(20)) == pdPASS) {
            handle_event(event);
            while (xQueueReceive(events_, &event, 0) == pdPASS) {
                handle_event(event);
            }
        }
        const uint32_t now = now_ms();
        handle_button(now);
        display_.animation_tick();
        if (now - last_heartbeat_ms >= 5000) {
            last_heartbeat_ms = now;
            ESP_LOGI(kTag, "heartbeat: state=%d gpio0=%d", (int)state_, gpio_get_level(static_cast<gpio_num_t>(config::kButtonGpio)));
        }

        if (end_pending_ && state_ == State::THINKING && audio_.uplink_pending() == 0) {
            end_pending_ = false;
            if (!network_.send_session_message("end", session_)) {
                audio_.abort_session();
                set_error("END FAILED");
            }
        }

        if (state_ == State::SPEAKING && now - speaking_started_ms_ > 90000) {
            ESP_LOGW(kTag, "speech playback timeout");
            abort_session();
        }
        if (auto_face_active_ &&
            (elapsed(now, auto_face_watchdog_ms_) ||
             (auto_face_restore_ms_ != 0 && elapsed(now, auto_face_restore_ms_)))) {
            clear_auto_face(true);
        }
        if (state_ == State::ERROR && elapsed(now, error_until_ms_)) {
            if (network_.websocket_connected()) {
                set_state(State::IDLE);
                // 不在这里重置 next_ota_check_ms_：
                // 首次连接的 60s 自检由 WsConnected 分支（first_ws_connected_ 守卫）负责，
                // 之后的 6h 周期由 OTA 分支负责。ERROR 恢复时若重置为 60s 会覆盖 6h，
                // 形成「WSS 断开 → ERROR → 60s 后 OTA → 断开 → ERROR」的 66s 重连循环。
            } else {
                set_state(State::CONNECTING);
                next_reconnect_ms_ = now;
            }
        }
        if (ota_.self_check_expired() && provisioned_) {
            ESP_LOGE(kTag, "new firmware self-check timed out; rolling back");
            esp_ota_mark_app_invalid_rollback_and_reboot();
        }

        if (provisioned_ && network_initialized_ && state_ != State::UNPROVISIONED && state_ != State::OTA) {
            if (!network_.wifi_connected() && elapsed(now, next_reconnect_ms_)) {
                network_.reconnect_wifi();
                next_reconnect_ms_ = now + 5000;
            } else if (network_.wifi_connected() && !network_.websocket_connected() &&
                       (state_ == State::CONNECTING || state_ == State::IDLE) && elapsed(now, next_reconnect_ms_)) {
                esp_err_t err = network_.start_websocket();
                if (err != ESP_OK) {
                    ESP_LOGW(kTag, "start_websocket: %s", esp_err_to_name(err));
                }
                next_reconnect_ms_ = now + (err == ESP_ERR_INVALID_STATE ? 2000 : 5000);
            }
            if (network_.websocket_connected() && elapsed(now, next_ping_ms_) && state_ != State::UNPROVISIONED) {
                network_.send_ping(state_ == State::IDLE || state_ == State::CONNECTING ? 0 : session_);
                next_ping_ms_ = now + 20000;
            }
            if (state_ == State::IDLE && network_.websocket_connected() && elapsed(now, next_ota_check_ms_)) {
                set_state(State::OTA);
                network_.stop_websocket();
                esp_err_t err = ota_.check_and_update(config_);
                if (err != ESP_OK && err != ESP_ERR_NOT_FOUND) {
                    ESP_LOGW(kTag, "OTA check failed: %s", esp_err_to_name(err));
                }
                next_ota_check_ms_ = now_ms() + 6UL * 60UL * 60UL * 1000UL;
                set_state(State::CONNECTING);
                next_reconnect_ms_ = now_ms() + 1000;
            }
        }
    }
}

void Control::handle_event(const Event &event)
{
    const uint32_t now = now_ms();
    switch (event.type) {
        case EventType::WifiConnected:
            wifi_connected_ = true;
            network_.start_sntp();
            if (state_ != State::UNPROVISIONED && state_ != State::OTA) {
                set_state(State::CONNECTING);
                next_reconnect_ms_ = now + 500;
            }
            break;
        case EventType::WifiDisconnected:
            wifi_connected_ = false;
            if (state_ != State::UNPROVISIONED && state_ != State::OTA) {
                set_error("WIFI DISCONNECTED");
                audio_.abort_session();
                network_.stop_websocket();
                next_reconnect_ms_ = now + 2000;
            }
            break;
        case EventType::WsConnected:
            if (state_ == State::UNPROVISIONED || state_ == State::OTA || !network_.websocket_connected()) {
                break;
            }
            if (provisioned_ && network_.send_hello()) {
                text_[0] = '\0';
                set_state(State::IDLE);
                ota_.confirm_running();
                next_ping_ms_ = now + 20000;
                if (!first_ws_connected_) {
                    // 仅在首次连接成功时排 60s 后的首次 OTA 自检（验证固件健康）。
                    // 之后每次自检由 :189 推到 6h；若在这里无条件重置，
                    // 会把 :189 的 6h 覆盖为 60s，形成 66s 重连循环。
                    next_ota_check_ms_ = now + 60000;
                    first_ws_connected_ = true;
                }
            } else {
                set_error("HELLO FAILED");
            }
            break;
        case EventType::WsDisconnected:
            if (state_ != State::UNPROVISIONED && state_ != State::OTA) {
                audio_.abort_session();
                set_error("WS DISCONNECTED");
                next_reconnect_ms_ = now + 2500;
            }
            break;
        case EventType::AudioFrame:
            if (state_ == State::LISTENING && session_matches(event.session)) {
                recording_ms_ = now - listening_started_ms_;
                energy_total_ += event.value;
                ++energy_frames_;
                if (event.value > peak_energy_) peak_energy_ = event.value;
                if (event.value >= config::kVadThreshold) {
                    speech_ms_ += config::kFrameMs;
                    silence_ms_ = 0;
                } else if (speech_ms_ >= config::kVadMinSpeechMs) {
                    silence_ms_ += config::kFrameMs;
                } else {
                    silence_ms_ = 0;
                }
                if (recording_ms_ >= config::kMaxRecordingMs ||
                    (speech_ms_ >= config::kVadMinSpeechMs && silence_ms_ >= config::kVadSilenceMs)) {
                    finish_listening();
                }
            }
            break;
        case EventType::PlaybackFinished:
            if (state_ == State::SPEAKING && session_matches(event.session)) {
                audio_.abort_session();
                set_state(State::IDLE);
                if (auto_face_active_ && auto_face_session_ == event.session) {
                    auto_face_restore_ms_ = now + 8000;
                }
            }
            break;
        case EventType::AudioError:
            audio_.abort_session();
            clear_auto_face(false);
            if (network_.websocket_connected()) set_state(State::IDLE);
            else set_error("AUDIO ERROR");
            break;
        case EventType::AsrResult:
        case EventType::LlmDelta:
            // OLED 只保留状态页；中文识别与回复正文不显示，也不逐 token 重绘。
            break;
        case EventType::AudioStart:
            if (session_matches(event.session) && state_ == State::THINKING) {
                speaking_started_ms_ = now;
                set_state(State::SPEAKING);
            }
            break;
        case EventType::AudioEnd:
            break;
        case EventType::Done:
            if (session_matches(event.session)) {
                if (state_ == State::SPEAKING) {
                    audio_.end_playback(event.session);
                } else if (state_ == State::THINKING || state_ == State::LISTENING) {
                    audio_.abort_session();
                    set_state(State::IDLE);
                    if (auto_face_active_ && auto_face_session_ == event.session) {
                        auto_face_restore_ms_ = now + 8000;
                    }
                }
            }
            break;
        case EventType::ServerError:
            if (event.session == 0 || session_matches(event.session)) {
                audio_.abort_session();
                set_error("SERVER ERROR");
                if (event.text[0]) {
                    copy_text(text_, sizeof(text_), event.text);
                    display_.show(state_, text_);
                }
            }
            break;
        case EventType::Panel: {
            // event.text = "emotion|text"；emotion 为 "clear" 时撤掉自定义画面。
            const std::string payload(event.text);
            const size_t split = payload.find('|');
            const std::string emotion = split == std::string::npos ? payload : payload.substr(0, split);
            const std::string note = split == std::string::npos ? std::string() : payload.substr(split + 1);
            if (event.value <= 100) {
                audio_.set_volume(static_cast<int>(event.value));
                DevicePrefs::set_u8("volume", static_cast<uint8_t>(event.value));
            }
            if (!emotion.empty()) {
                if ((state_ == State::THINKING || state_ == State::SPEAKING) && session_ != 0) {
                    auto_face_blocked_session_ = session_;
                }
                clear_auto_face(false);
            }
            if (emotion == "clear") {
                display_.clear_face();
                DevicePrefs::erase("face_emotion");
                DevicePrefs::erase("face_text");
                display_.show(state_, text_);
            } else if (!emotion.empty()) {
                display_.show_face(emotion.c_str(), note.c_str());
                DevicePrefs::set_str("face_emotion", emotion);
                DevicePrefs::set_str("face_text", note);
            }
            network_.send_panel_ack(true, audio_.volume());
            break;
        }
        case EventType::PanelAuto:
            if (!session_matches(event.session) || auto_face_blocked_session_ == event.session ||
                (state_ != State::THINKING && state_ != State::SPEAKING)) {
                break;
            }
            auto_face_active_ = true;
            auto_face_session_ = event.session;
            auto_face_restore_ms_ = 0;
            auto_face_watchdog_ms_ = now + (event.value != 0 ? event.value : 90000);
            ESP_LOGI(kTag, "auto face: session=%u emotion=%s", event.session, event.text);
            display_.show_auto_face(event.text);
            break;
        case EventType::OtaCheck:
            // 只提前现有检查时间；真正下载仍由控制循环在 IDLE 且 WSS 已连接时执行。
            next_ota_check_ms_ = now;
            break;
        case EventType::WakeWordDetected:
            if (state_ == State::IDLE) begin_listening();
            break;
    }
}

void Control::handle_button(uint32_t now)
{
    const bool released = gpio_get_level(static_cast<gpio_num_t>(config::kButtonGpio)) != 0;
    if (released != button_last_raw_) {
        ESP_LOGI(kTag, "button raw change: %s (state=%d)", released ? "released" : "pressed", (int)state_);
        button_last_raw_ = released;
        button_changed_ms_ = now;
    }
    if (released != button_stable_ && now - button_changed_ms_ >= config::kButtonDebounceMs) {
        button_stable_ = released;
        if (!button_stable_) {
            ESP_LOGI(kTag, "button pressed");
            button_pressed_ms_ = now;
            long_press_handled_ = false;
        } else if (!long_press_handled_) {
            ESP_LOGI(kTag, "button released, state=%d", (int)state_);
            if (state_ == State::IDLE) {
                begin_listening();
            } else if (state_ == State::LISTENING || state_ == State::THINKING || state_ == State::SPEAKING) {
                abort_session();
            }
        }
    }
    if (!button_stable_ && !long_press_handled_ && now - button_pressed_ms_ >= config::kButtonLongPressMs) {
        long_press_handled_ = true;
        enter_provisioning();
    }
}

void Control::enter_provisioning()
{
    log_memory("provision_entry");
    const bool updating_network = provisioned_;
    const DeviceConfig previous_config = config_;
    network_.stop_websocket();
    audio_.abort_session();
    clear_auto_face(false);
    provisioned_ = false;
    copy_text(text_, sizeof(text_), "BLE / UART0");
    set_state(State::UNPROVISIONED);

    provisioning_.begin();
    ble_.start(DeviceConfigStore::make_device_id());
    log_memory("ble_started");

    PendingConfig pending;
    bool ble_active = true;
    const uint32_t ble_deadline = now_ms() + 300000;
    for (;;) {
        if (ble_active) {
            BlePendingConfig from_phone;
            if (ble_.take_pending(from_phone)) {
                pending.config = from_phone.config;
                pending.pairing_token = from_phone.pairing_token;
                break;
            }
            if (elapsed(now_ms(), ble_deadline)) {
                ble_.stop();
                ble_active = false;
            }
        }
        if (provisioning_.try_receive(pending, 200) == ESP_OK) {
            break;
        }
    }

    pending.config.config_version = previous_config.config_version;
    if (updating_network) {
        // 已绑定设备只改网络参数；设备身份、归属令牌和配置版本必须延续。
        pending.config.device_id = previous_config.device_id;
        pending.config.device_token = previous_config.device_token;
    }

    auto restore_previous_network = [&](const char *failure_state, const char *detail) {
        ble_.notify_state(failure_state, detail);
        // 给 NimBLE 留出发送最后一次通知的时间，再恢复日志和旧网络。
        vTaskDelay(pdMS_TO_TICKS(350));
        ble_.stop();
        log_memory("ble_stopped_restore");
        provisioning_.restore_logs();
        config_ = previous_config;
        if (std::strcmp(failure_state, "storage_failed") == 0) {
            DeviceConfig restore_config = previous_config;
            const esp_err_t nv_restore_err = store_.save(restore_config);
            if (nv_restore_err != ESP_OK) {
                ESP_LOGE(kTag, "restore previous NVS config failed: %s", esp_err_to_name(nv_restore_err));
            }
        }
        esp_err_t restore_err = ESP_OK;
        if (network_initialized_) {
            restore_err = network_.update_config(config_);
        } else {
            restore_err = network_.init(config_);
            network_initialized_ = restore_err == ESP_OK;
        }
        if (restore_err != ESP_OK) {
            ESP_LOGE(kTag, "restore previous WiFi failed: %s", esp_err_to_name(restore_err));
        }
        network_.set_device_token(previous_config.device_token);
        provisioned_ = true;
        wifi_connected_ = false;
        text_[0] = '\0';
        set_state(State::CONNECTING);
        next_reconnect_ms_ = now_ms();
    };

    config_ = pending.config;
    copy_text(text_, sizeof(text_), updating_network ? "UPDATING NETWORK" : "CLAIMING DEVICE");
    set_state(State::CONNECTING);
    ble_.notify_state("wifi_connecting");

    if (!network_initialized_) {
        const esp_err_t init_err = network_.init(config_);
        if (init_err != ESP_OK) {
            if (updating_network) {
                restore_previous_network("wifi_config_failed", esp_err_to_name(init_err));
            } else {
                ble_.notify_state("error", "wifi_init_failed");
                set_error("WIFI INIT FAILED");
            }
            return;
        }
        network_initialized_ = true;
    } else {
        const esp_err_t update_err = network_.update_config(config_);
        if (update_err != ESP_OK) {
            if (updating_network) {
                restore_previous_network("wifi_config_failed", esp_err_to_name(update_err));
            } else {
                ble_.notify_state("error", "wifi_config_failed");
                set_error("WIFI CONFIG FAILED");
            }
            return;
        }
    }

    const uint32_t update_deadline = now_ms() + 120000;
    bool sntp_started = false;
    bool wifi_reported = false;
    bool authorization_retry_reported = false;
    bool authorization_memory_reported = false;
    for (;;) {
        const uint32_t now = now_ms();
        if (updating_network && elapsed(now, update_deadline)) {
            if (!network_.wifi_connected()) {
                char detail[64];
                std::snprintf(detail, sizeof(detail), "disconnect_reason=%d", network_.last_disconnect_reason());
                restore_previous_network("wifi_timeout", detail);
            } else {
                const bool secure = config_.server_url.rfind("https://", 0) == 0 ||
                                    config_.server_url.rfind("wss://", 0) == 0;
                restore_previous_network(
                    "authorization_timeout", secure && !network_.time_synchronized() ? "sntp_timeout" : "authorization_failed");
            }
            return;
        }
        if (network_.wifi_connected()) {
            if (!wifi_reported) {
                ble_.notify_state("wifi_ok");
                wifi_reported = true;
            }
            if (!sntp_started) {
                network_.start_sntp();
                sntp_started = true;
            }
            const bool secure = config_.server_url.rfind("https://", 0) == 0 ||
                                config_.server_url.rfind("wss://", 0) == 0;
            if (!authorization_memory_reported) {
                log_memory("before_authorization");
                authorization_memory_reported = true;
            }
            const esp_err_t authorize_result = (!secure || network_.time_synchronized())
                ? (updating_network
                       ? provisioning_.authorize_network(config_, pending.pairing_token)
                       : provisioning_.claim(config_, pending.pairing_token))
                : ESP_ERR_INVALID_STATE;
            if (authorize_result == ESP_OK) {
                const esp_err_t save_err = store_.save(config_);
                if (save_err == ESP_OK) {
                    ble_.notify_state(updating_network ? "network_updated" : "claimed");
                    vTaskDelay(pdMS_TO_TICKS(350));
                    ble_.stop();
                    log_memory("ble_stopped_success");
                    provisioning_.restore_logs();
                    network_.set_device_token(config_.device_token);
                    provisioned_ = true;
                    text_[0] = '\0';
                    set_state(State::CONNECTING);
                    next_reconnect_ms_ = now_ms();
                    return;
                }
                ESP_LOGE(kTag, "failed to save network configuration: %s", esp_err_to_name(save_err));
                if (updating_network) {
                    restore_previous_network("storage_failed", esp_err_to_name(save_err));
                    return;
                }
            } else if (updating_network && authorize_result == ESP_ERR_INVALID_ARG) {
                restore_previous_network("authorization_failed", esp_err_to_name(authorize_result));
                return;
            } else if (!authorization_retry_reported) {
                ble_.notify_state(updating_network ? "authorization_retry" : "claim_failed", "retrying");
                authorization_retry_reported = true;
            }
        } else {
            sntp_started = false;
            wifi_reported = false;
            const esp_err_t reconnect_err = network_.reconnect_wifi();
            if (reconnect_err != ESP_OK) {
                ESP_LOGW(kTag, "WiFi reconnect failed: %s", esp_err_to_name(reconnect_err));
            }
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

void Control::begin_listening()
{
    if (state_ != State::IDLE || !network_.websocket_connected()) return;
    clear_auto_face(false);
    auto_face_blocked_session_ = 0;
    ++session_;
    if (session_ == 0) ++session_;
    g_session.store(session_);
    recording_ms_ = speech_ms_ = silence_ms_ = 0;
    energy_total_ = 0;
    energy_frames_ = 0;
    peak_energy_ = 0;
    text_[0] = '\0';
    audio_.begin_session(session_);
    if (network_.send_start(session_)) {
        listening_started_ms_ = now_ms();
        set_state(State::LISTENING);
        ESP_LOGI(kTag, "begin_listening: session=%u started", session_);
    } else {
        ESP_LOGE(kTag, "begin_listening: send_session_message(start) failed");
        audio_.abort_session();
        set_error("START FAILED");
    }
}

void Control::finish_listening()
{
    if (state_ != State::LISTENING) return;
    const uint32_t average_energy = energy_frames_ ? energy_total_ / energy_frames_ : 0;
    ESP_LOGI(kTag,
             "finish_listening: session=%u, recording=%ums speech=%ums silence=%ums energy_avg=%u energy_peak=%u",
             session_, recording_ms_, speech_ms_, silence_ms_, average_energy, peak_energy_);
    end_pending_ = true;
    set_state(State::THINKING);
}

void Control::abort_session()
{
    ESP_LOGI(kTag, "abort_session: session=%u state=%d", session_, (int)state_);
    if (network_.websocket_connected() && session_ != 0) {
        network_.send_session_message("abort", session_);
    }
    audio_.abort_session();
    clear_auto_face(false);
    auto_face_blocked_session_ = 0;
    text_[0] = '\0';
    if (network_.websocket_connected()) set_state(State::IDLE);
    else set_error("CONNECTION LOST");
}

void Control::set_state(State next)
{
    if (next != State::THINKING) {
        end_pending_ = false;
    }
    state_ = next;
    g_state.store(next);
    display_.show(next, text_);
}

void Control::clear_auto_face(bool redraw)
{
    const bool was_active = auto_face_active_;
    auto_face_active_ = false;
    auto_face_session_ = 0;
    auto_face_restore_ms_ = 0;
    auto_face_watchdog_ms_ = 0;
    display_.clear_auto_face();
    if (redraw && was_active) display_.show(state_, text_);
}

void Control::set_error(const char *message)
{
    clear_auto_face(false);
    copy_text(text_, sizeof(text_), message);
    set_state(State::ERROR);
    error_until_ms_ = now_ms() + 6000;
}

bool Control::session_matches(uint32_t value) const
{
    return value != 0 && value == session_;
}

} // namespace app
