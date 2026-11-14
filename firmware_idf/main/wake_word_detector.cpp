#include "wake_word_detector.h"

#include <algorithm>
#include <cstring>
#include "app_control.h"
#include "device_config.h"
#include "esp_log.h"
#include "esp_mn_models.h"
#include "esp_mn_speech_commands.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "wake";
constexpr char kDefaultWakeWord[] = "小绫小绫";
constexpr char kDefaultWakeCommand[] = "xiao ling xiao ling";
constexpr int kCommandId = 1;
constexpr int kDetectionDurationMs = 3000;
constexpr float kDetectionThreshold = 0.20f;
constexpr UBaseType_t kPcmQueueLength = 8;

bool valid_command(const std::string &command)
{
    if (command.empty() || command.size() > 192 || command.front() == ' ' || command.back() == ' ') {
        return false;
    }
    bool previous_space = false;
    for (const unsigned char ch : command) {
        if (ch == ' ') {
            if (previous_space) return false;
            previous_space = true;
        } else {
            if (ch < 'a' || ch > 'z') return false;
            previous_space = false;
        }
    }
    return true;
}
} // namespace

esp_err_t WakeWordDetector::init()
{
    wake_word_ = DevicePrefs::get_str("wake_word");
    wake_command_ = DevicePrefs::get_str("wake_command");
    if (wake_word_.empty()) wake_word_ = kDefaultWakeWord;
    if (wake_command_.empty()) wake_command_ = kDefaultWakeCommand;

    models_ = esp_srmodel_init("model");
    if (!models_ || models_->num <= 0) {
        ESP_LOGE(kTag, "no ESP-SR models found in model partition");
        return ESP_ERR_NOT_FOUND;
    }
    char *model_name = esp_srmodel_filter(models_, ESP_MN_PREFIX, ESP_MN_CHINESE);
    if (!model_name) {
        ESP_LOGE(kTag, "Chinese MultiNet model not found");
        return ESP_ERR_NOT_FOUND;
    }
    multinet_ = esp_mn_handle_from_name(model_name);
    if (!multinet_) return ESP_ERR_NOT_SUPPORTED;
    model_data_ = multinet_->create(model_name, kDetectionDurationMs);
    if (!model_data_) return ESP_ERR_NO_MEM;
    multinet_->set_det_threshold(model_data_, kDetectionThreshold);
    if (multinet_->get_samp_rate(model_data_) != config::kSampleRate) {
        ESP_LOGE(kTag, "MultiNet sample rate mismatch");
        return ESP_ERR_NOT_SUPPORTED;
    }
    chunk_samples_ = multinet_->get_samp_chunksize(model_data_);
    if (chunk_samples_ <= 0 || chunk_samples_ > 4096) return ESP_ERR_INVALID_STATE;
    input_.reserve(chunk_samples_ + config::kSamplesPerFrame);

    esp_err_t err = esp_mn_commands_alloc(multinet_, model_data_);
    if (err != ESP_OK) return err;
    err = apply_command(wake_command_);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "saved wake command rejected, restoring default");
        wake_word_ = kDefaultWakeWord;
        wake_command_ = kDefaultWakeCommand;
        err = apply_command(wake_command_);
        if (err == ESP_OK) {
            DevicePrefs::set_str("wake_word", wake_word_);
            DevicePrefs::set_str("wake_command", wake_command_);
        }
    }
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "default wake command rejected: %s", esp_err_to_name(err));
        return err;
    }

    pcm_queue_ = xQueueCreate(kPcmQueueLength, sizeof(PcmFrame));
    update_queue_ = xQueueCreate(1, sizeof(UpdateRequest *));
    update_mutex_ = xSemaphoreCreateMutex();
    if (!pcm_queue_ || !update_queue_ || !update_mutex_) return ESP_ERR_NO_MEM;
    if (xTaskCreate(task_entry, "wake_mn7", 12288, this, 3, nullptr) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ready_.store(true);
    ESP_LOGI(kTag, "MultiNet ready: model=%s chunk=%d threshold=%.2f wake=%s",
             model_name, chunk_samples_, kDetectionThreshold, wake_word_.c_str());
    return ESP_OK;
}

void WakeWordDetector::feed(const int16_t *samples, size_t count)
{
    if (!ready_.load() || !samples || count != config::kSamplesPerFrame) return;
    PcmFrame frame = {};
    frame.count = count;
    std::memcpy(frame.samples, samples, count * sizeof(int16_t));
    if (xQueueSend(pcm_queue_, &frame, 0) != pdPASS) {
        dropped_frames_.fetch_add(1);
    }
}

esp_err_t WakeWordDetector::update_wake_word(
    const std::string &word, const std::string &command, const char **error)
{
    if (error) *error = nullptr;
    if (word.empty() || !valid_command(command)) {
        if (error) *error = "invalid_wake_command";
        return ESP_ERR_INVALID_ARG;
    }
    if (!ready_.load()) {
        if (error) *error = "wake_engine_unavailable";
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(update_mutex_, portMAX_DELAY) != pdPASS) {
        if (error) *error = "wake_engine_unavailable";
        return ESP_ERR_INVALID_STATE;
    }
    SemaphoreHandle_t done = xSemaphoreCreateBinary();
    if (!done) {
        xSemaphoreGive(update_mutex_);
        if (error) *error = "wake_engine_unavailable";
        return ESP_ERR_NO_MEM;
    }
    UpdateRequest request = {&word, &command, done, ESP_FAIL, "wake_engine_unavailable"};
    UpdateRequest *queued = &request;
    if (xQueueSend(update_queue_, &queued, portMAX_DELAY) != pdPASS) {
        vSemaphoreDelete(done);
        xSemaphoreGive(update_mutex_);
        if (error) *error = "wake_engine_unavailable";
        return ESP_FAIL;
    }
    xSemaphoreTake(done, portMAX_DELAY);
    vSemaphoreDelete(done);
    xSemaphoreGive(update_mutex_);
    if (error) *error = request.error;
    return request.result;
}

void WakeWordDetector::task_entry(void *arg)
{
    static_cast<WakeWordDetector *>(arg)->run();
}

void WakeWordDetector::run()
{
    PcmFrame frame = {};
    for (;;) {
        UpdateRequest *request = nullptr;
        if (xQueueReceive(update_queue_, &request, 0) == pdPASS && request) {
            process_update(*request);
        }

        const bool got_frame = xQueueReceive(pcm_queue_, &frame, pdMS_TO_TICKS(20)) == pdPASS;
        const bool idle = app::state() == app::State::IDLE;
        if (wait_for_leave_idle_) {
            if (idle) continue;
            wait_for_leave_idle_ = false;
        }
        if (!idle) {
            if (was_idle_ || got_frame) reset_detector();
            was_idle_ = false;
            continue;
        }
        if (!was_idle_) {
            reset_detector();
            was_idle_ = true;
            continue;
        }
        if (!got_frame) continue;

        input_.insert(input_.end(), frame.samples, frame.samples + frame.count);
        while (input_.size() >= static_cast<size_t>(chunk_samples_)) {
            const esp_mn_state_t state = multinet_->detect(model_data_, input_.data());
            if (state == ESP_MN_STATE_DETECTED) {
                esp_mn_results_t *results = multinet_->get_results(model_data_);
                bool detected = false;
                if (results) {
                    for (int i = 0; i < results->num; ++i) {
                        if (results->command_id[i] == kCommandId) {
                            detected = true;
                            ESP_LOGI(kTag, "wake detected: %s probability=%.3f",
                                     wake_word_.c_str(), results->prob[i]);
                            break;
                        }
                    }
                }
                multinet_->clean(model_data_);
                input_.clear();
                if (detected && app::state() == app::State::IDLE) {
                    if (app::post_event(app::EventType::WakeWordDetected)) {
                        wait_for_leave_idle_ = true;
                        flush_pcm();
                    }
                }
                break;
            }
            if (state == ESP_MN_STATE_TIMEOUT) multinet_->clean(model_data_);
            input_.erase(input_.begin(), input_.begin() + chunk_samples_);
        }
    }
}

void WakeWordDetector::process_update(UpdateRequest &request)
{
    const std::string previous_word = wake_word_;
    const std::string previous_command = wake_command_;
    reset_detector();
    wait_for_leave_idle_ = false;

    request.result = apply_command(*request.command);
    if (request.result != ESP_OK) {
        request.error = request.result == ESP_ERR_INVALID_ARG
            ? "invalid_wake_command" : "wake_engine_unavailable";
        const esp_err_t rollback = apply_command(previous_command);
        if (rollback != ESP_OK) {
            ESP_LOGE(kTag, "failed to restore previous wake command: %s", esp_err_to_name(rollback));
            ready_.store(false);
            request.error = "wake_engine_unavailable";
        }
        xSemaphoreGive(request.done);
        return;
    }

    esp_err_t storage = DevicePrefs::set_str("wake_word", *request.word);
    if (storage == ESP_OK) storage = DevicePrefs::set_str("wake_command", *request.command);
    if (storage != ESP_OK) {
        ESP_LOGE(kTag, "wake word storage failed: %s", esp_err_to_name(storage));
        DevicePrefs::set_str("wake_word", previous_word);
        DevicePrefs::set_str("wake_command", previous_command);
        const esp_err_t rollback = apply_command(previous_command);
        if (rollback != ESP_OK) ready_.store(false);
        request.result = storage;
        request.error = rollback == ESP_OK ? "wake_word_storage_failed" : "wake_engine_unavailable";
        xSemaphoreGive(request.done);
        return;
    }

    wake_word_ = *request.word;
    wake_command_ = *request.command;
    request.result = ESP_OK;
    request.error = nullptr;
    ESP_LOGI(kTag, "wake word updated and saved: %s", wake_word_.c_str());
    xSemaphoreGive(request.done);
}

esp_err_t WakeWordDetector::apply_command(const std::string &command)
{
    if (!valid_command(command)) return ESP_ERR_INVALID_ARG;
    esp_err_t err = esp_mn_commands_clear();
    if (err != ESP_OK) return err;
    err = esp_mn_commands_add(kCommandId, command.c_str());
    if (err != ESP_OK) return ESP_ERR_INVALID_ARG;
    esp_mn_error_t *errors = esp_mn_commands_update();
    if (errors && errors->num > 0) return ESP_ERR_INVALID_ARG;
    multinet_->clean(model_data_);
    input_.clear();
    return ESP_OK;
}

void WakeWordDetector::flush_pcm()
{
    PcmFrame discarded;
    while (xQueueReceive(pcm_queue_, &discarded, 0) == pdPASS) {
    }
}

void WakeWordDetector::reset_detector()
{
    flush_pcm();
    input_.clear();
    if (multinet_ && model_data_) multinet_->clean(model_data_);
}
