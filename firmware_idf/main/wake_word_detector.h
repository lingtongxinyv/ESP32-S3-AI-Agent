#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>
#include "app_config.h"
#include "esp_err.h"
#include "esp_mn_iface.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "model_path.h"

class WakeWordDetector {
public:
    esp_err_t init();
    bool available() const { return ready_.load(); }

    /** 仅供 I2S RX 投递一帧 PCM；队列满时丢弃，不阻塞音频任务。 */
    void feed(const int16_t *samples, size_t count);

    /** 串行更新识别命令和 NVS；error 返回稳定的配置 ACK 错误码。 */
    esp_err_t update_wake_word(const std::string &word, const std::string &command, const char **error);

private:
    struct PcmFrame {
        size_t count;
        int16_t samples[config::kSamplesPerFrame];
    };

    struct UpdateRequest {
        const std::string *word;
        const std::string *command;
        SemaphoreHandle_t done;
        esp_err_t result;
        const char *error;
    };

    static void task_entry(void *arg);
    void run();
    void process_update(UpdateRequest &request);
    esp_err_t apply_command(const std::string &command);
    void flush_pcm();
    void reset_detector();

    srmodel_list_t *models_ = nullptr;
    const esp_mn_iface_t *multinet_ = nullptr;
    model_iface_data_t *model_data_ = nullptr;
    int chunk_samples_ = 0;
    QueueHandle_t pcm_queue_ = nullptr;
    QueueHandle_t update_queue_ = nullptr;
    SemaphoreHandle_t update_mutex_ = nullptr;
    std::vector<int16_t> input_;
    std::string wake_word_;
    std::string wake_command_;
    std::atomic<bool> ready_{false};
    std::atomic<uint32_t> dropped_frames_{0};
    bool was_idle_ = false;
    bool wait_for_leave_idle_ = false;
};
