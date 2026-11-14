#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include "app_config.h"
#include "driver/i2s_types.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

class WakeWordDetector;

struct AudioFrame {
    uint32_t session;
    uint32_t sequence;
    uint32_t energy;
    int16_t pcm[config::kSamplesPerFrame];
};

class AudioEngine {
public:
    explicit AudioEngine(WakeWordDetector &wake) : wake_(wake) {}
    esp_err_t init();
    void begin_session(uint32_t session);
    void start_playback(uint32_t session);
    void end_playback(uint32_t session);
    void abort_session();

    bool enqueue_downlink(uint32_t session, uint32_t sequence, const uint8_t *pcm, size_t length);
    bool dequeue_uplink(AudioFrame **frame, TickType_t timeout);
    void release_uplink(AudioFrame *frame);
    size_t uplink_pending() const;

    /** 喇叭音量 0-100（软件增益，直接缩放下发 PCM）。 */
    void set_volume(int percent);
    int volume() const { return volume_.load(); }

private:
    static void rx_task_entry(void *arg);
    static void tx_task_entry(void *arg);
    void rx_task();
    void tx_task();
    void flush_queue(QueueHandle_t queue, QueueHandle_t free_queue);
    void report_audio_error(uint32_t error);

    WakeWordDetector &wake_;
    i2s_chan_handle_t rx_channel_ = nullptr;
    i2s_chan_handle_t tx_channel_ = nullptr;
    QueueHandle_t uplink_free_ = nullptr;
    QueueHandle_t uplink_queue_ = nullptr;
    QueueHandle_t downlink_free_ = nullptr;
    QueueHandle_t downlink_queue_ = nullptr;
    AudioFrame *uplink_frames_ = nullptr;
    AudioFrame *downlink_frames_ = nullptr;
    std::atomic<uint32_t> current_session_{0};
    std::atomic<uint32_t> uplink_sequence_{0};
    std::atomic<bool> uplink_inflight_{false};
    std::atomic<bool> playback_active_{false};
    std::atomic<bool> playback_started_{false};
    std::atomic<bool> playback_ended_{false};
    std::atomic<int> volume_{100};
    uint32_t downlink_sequence_ = 0;
    bool have_downlink_sequence_ = false;
    std::atomic<uint32_t> downlink_received_{0};
    std::atomic<uint32_t> downlink_dropped_{0};
    std::atomic<uint32_t> playback_frames_{0};
    std::atomic<uint32_t> playback_underruns_{0};
    uint32_t last_error_log_ms_ = 0;
};
