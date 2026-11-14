#include "audio_engine.h"

#include <algorithm>
#include <cstring>
#include "app_control.h"
#include "driver/i2s_common.h"
#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "wake_word_detector.h"

namespace {
constexpr char kTag[] = "audio";
DRAM_ATTR int32_t rx_raw[config::kSamplesPerFrame];
DRAM_ATTR int16_t rx_mono[config::kSamplesPerFrame];
DRAM_ATTR int16_t tx_stereo[config::kSamplesPerFrame * 2];
DRAM_ATTR int16_t tx_silence[config::kSamplesPerFrame * 2];

uint32_t now_ms()
{
    return static_cast<uint32_t>(esp_timer_get_time() / 1000);
}
} // namespace

esp_err_t AudioEngine::init()
{
    uplink_frames_ = static_cast<AudioFrame *>(heap_caps_calloc(
        config::kUplinkFrames, sizeof(AudioFrame), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    downlink_frames_ = static_cast<AudioFrame *>(heap_caps_calloc(
        config::kDownlinkFrames, sizeof(AudioFrame), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!uplink_frames_ || !downlink_frames_) {
        ESP_LOGE(kTag, "PSRAM frame pool allocation failed");
        return ESP_ERR_NO_MEM;
    }

    uplink_free_ = xQueueCreate(config::kUplinkFrames, sizeof(AudioFrame *));
    uplink_queue_ = xQueueCreate(config::kUplinkFrames, sizeof(AudioFrame *));
    downlink_free_ = xQueueCreate(config::kDownlinkFrames, sizeof(AudioFrame *));
    downlink_queue_ = xQueueCreate(config::kDownlinkFrames, sizeof(AudioFrame *));
    if (!uplink_free_ || !uplink_queue_ || !downlink_free_ || !downlink_queue_) {
        return ESP_ERR_NO_MEM;
    }
    for (size_t i = 0; i < config::kUplinkFrames; ++i) {
        AudioFrame *frame = &uplink_frames_[i];
        xQueueSend(uplink_free_, &frame, 0);
    }
    for (size_t i = 0; i < config::kDownlinkFrames; ++i) {
        AudioFrame *frame = &downlink_frames_[i];
        xQueueSend(downlink_free_, &frame, 0);
    }

    i2s_chan_config_t rx_channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    rx_channel_config.dma_desc_num = 6;
    rx_channel_config.dma_frame_num = config::kSamplesPerFrame;
    esp_err_t err = i2s_new_channel(&rx_channel_config, nullptr, &rx_channel_);
    if (err != ESP_OK) return err;

    i2s_std_config_t rx_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(config::kSampleRate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = GPIO_NUM_NC,
            .bclk = static_cast<gpio_num_t>(config::kMicBclk),
            .ws = static_cast<gpio_num_t>(config::kMicWs),
            .dout = GPIO_NUM_NC,
            .din = static_cast<gpio_num_t>(config::kMicData),
            .invert_flags = {},
        },
    };
    rx_config.slot_cfg.slot_mode = I2S_SLOT_MODE_MONO;
    rx_config.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
    rx_config.slot_cfg.slot_bit_width = I2S_SLOT_BIT_WIDTH_32BIT;
    err = i2s_channel_init_std_mode(rx_channel_, &rx_config);
    if (err != ESP_OK) return err;

    i2s_chan_config_t tx_channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    tx_channel_config.dma_desc_num = 6;
    tx_channel_config.dma_frame_num = config::kSamplesPerFrame;
    tx_channel_config.auto_clear = true;
    err = i2s_new_channel(&tx_channel_config, &tx_channel_, nullptr);
    if (err != ESP_OK) return err;

    i2s_std_config_t tx_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(config::kSampleRate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = GPIO_NUM_NC,
            .bclk = static_cast<gpio_num_t>(config::kSpeakerBclk),
            .ws = static_cast<gpio_num_t>(config::kSpeakerWs),
            .dout = static_cast<gpio_num_t>(config::kSpeakerData),
            .din = GPIO_NUM_NC,
            .invert_flags = {},
        },
    };
    err = i2s_channel_init_std_mode(tx_channel_, &tx_config);
    if (err != ESP_OK) return err;
    if ((err = i2s_channel_enable(rx_channel_)) != ESP_OK) return err;
    if ((err = i2s_channel_enable(tx_channel_)) != ESP_OK) return err;

    if (xTaskCreate(rx_task_entry, "audio_rx", 4096, this, 12, nullptr) != pdPASS ||
        xTaskCreate(tx_task_entry, "audio_tx", 4096, this, 11, nullptr) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(kTag, "I2S0 RX and I2S1 TX enabled at 16000 Hz, 20 ms frames");
    return ESP_OK;
}

void AudioEngine::begin_session(uint32_t session)
{
    abort_session();
    current_session_.store(session);
    uplink_sequence_.store(0);
}

void AudioEngine::start_playback(uint32_t session)
{
    if (session != current_session_.load() || app::state() != app::State::THINKING) {
        return;
    }
    playback_active_.store(false);
    flush_queue(downlink_queue_, downlink_free_);
    playback_started_.store(false);
    playback_ended_.store(false);
    have_downlink_sequence_ = false;
    downlink_sequence_ = 0;
    downlink_received_.store(0);
    downlink_dropped_.store(0);
    playback_frames_.store(0);
    playback_underruns_.store(0);
    playback_active_.store(true);
}

void AudioEngine::end_playback(uint32_t session)
{
    if (playback_active_.load() && session == current_session_.load()) {
        playback_ended_.store(true);
    }
}

void AudioEngine::abort_session()
{
    playback_active_.store(false);
    playback_started_.store(false);
    playback_ended_.store(false);
    flush_queue(uplink_queue_, uplink_free_);
    flush_queue(downlink_queue_, downlink_free_);
}

bool AudioEngine::enqueue_downlink(uint32_t session, uint32_t sequence, const uint8_t *pcm, size_t length)
{
    if (!playback_active_.load() || playback_ended_.load() || session != current_session_.load() ||
        length != config::kMonoFrameBytes || pcm == nullptr) {
        return false;
    }
    downlink_received_.fetch_add(1);
    if (have_downlink_sequence_ && sequence <= downlink_sequence_) {
        downlink_dropped_.fetch_add(1);
        return false;
    }
    AudioFrame *frame = nullptr;
    if (xQueueReceive(downlink_free_, &frame, 0) != pdPASS) {
        downlink_dropped_.fetch_add(1);
        return false;
    }
    frame->session = session;
    frame->sequence = sequence;
    frame->energy = 0;
    std::memcpy(frame->pcm, pcm, config::kMonoFrameBytes);
    if (xQueueSend(downlink_queue_, &frame, 0) != pdPASS) {
        xQueueSend(downlink_free_, &frame, 0);
        downlink_dropped_.fetch_add(1);
        return false;
    }
    downlink_sequence_ = sequence;
    have_downlink_sequence_ = true;
    return true;
}

void AudioEngine::set_volume(int percent)
{
    const int clamped = percent < 0 ? 0 : (percent > 100 ? 100 : percent);
    volume_.store(clamped);
}

bool AudioEngine::dequeue_uplink(AudioFrame **frame, TickType_t timeout)
{
    if (!frame || xQueueReceive(uplink_queue_, frame, timeout) != pdPASS) {
        return false;
    }
    uplink_inflight_.store(true);
    return true;
}

void AudioEngine::release_uplink(AudioFrame *frame)
{
    if (frame) {
        xQueueSend(uplink_free_, &frame, 0);
    }
    uplink_inflight_.store(false);
}

size_t AudioEngine::uplink_pending() const
{
    const size_t queued = uplink_queue_ ? uxQueueMessagesWaiting(uplink_queue_) : 0;
    return queued + (uplink_inflight_.load() ? 1 : 0);
}

void AudioEngine::rx_task_entry(void *arg)
{
    static_cast<AudioEngine *>(arg)->rx_task();
}

void AudioEngine::tx_task_entry(void *arg)
{
    static_cast<AudioEngine *>(arg)->tx_task();
}

void AudioEngine::rx_task()
{
    for (;;) {
        size_t bytes_read = 0;
        esp_err_t err = i2s_channel_read(rx_channel_, rx_raw, sizeof(rx_raw), &bytes_read, 100);
        if (err != ESP_OK || bytes_read != sizeof(rx_raw)) {
            report_audio_error(static_cast<uint32_t>(err));
            continue;
        }

        AudioFrame *frame = nullptr;
        const bool listening = app::state() == app::State::LISTENING;
        if (listening) {
            if (xQueueReceive(uplink_free_, &frame, 0) != pdPASS) {
                frame = nullptr;
            }
        }

        uint64_t absolute_sum = 0;
        for (size_t i = 0; i < config::kSamplesPerFrame; ++i) {
            const int16_t sample = static_cast<int16_t>(static_cast<uint32_t>(rx_raw[i]) >> 16);
            rx_mono[i] = sample;
            absolute_sum += sample < 0 ? -static_cast<int32_t>(sample) : sample;
            if (frame) {
                frame->pcm[i] = sample;
            }
        }
        const uint32_t energy = absolute_sum / config::kSamplesPerFrame;
        if (frame) {
            frame->session = current_session_.load();
            frame->sequence = uplink_sequence_.fetch_add(1);
            frame->energy = energy;
            if (xQueueSend(uplink_queue_, &frame, 0) != pdPASS) {
                xQueueSend(uplink_free_, &frame, 0);
            }
        }
        if (listening) {
            app::post_event(app::EventType::AudioFrame, current_session_.load(), energy);
        } else if (app::state() == app::State::IDLE) {
            wake_.feed(rx_mono, config::kSamplesPerFrame);
        }
    }
}

void AudioEngine::tx_task()
{
    for (;;) {
        AudioFrame *frame = nullptr;
        const bool active = playback_active_.load();
        const UBaseType_t queued = uxQueueMessagesWaiting(downlink_queue_);
        const bool buffered = playback_started_.load() || queued >= config::kJitterFrames || playback_ended_.load();
        if (active && buffered && xQueueReceive(downlink_queue_, &frame, 0) == pdPASS && frame) {
            if (frame->session == current_session_.load() && playback_active_.load()) {
                const int gain = volume_.load();
                for (size_t i = 0; i < config::kSamplesPerFrame; ++i) {
                    const int16_t scaled = static_cast<int16_t>(frame->pcm[i] * gain / 100);
                    tx_stereo[i * 2] = scaled;
                    tx_stereo[i * 2 + 1] = scaled;
                }
                playback_started_.store(true);
                playback_frames_.fetch_add(1);
                size_t written = 0;
                esp_err_t err = i2s_channel_write(tx_channel_, tx_stereo, sizeof(tx_stereo), &written, 100);
                if (err != ESP_OK || written != sizeof(tx_stereo)) {
                    report_audio_error(static_cast<uint32_t>(err));
                }
            }
            xQueueSend(downlink_free_, &frame, 0);
        } else {
            if (active && playback_started_.load() && !playback_ended_.load()) {
                playback_underruns_.fetch_add(1);
            }
            size_t written = 0;
            i2s_channel_write(tx_channel_, tx_silence, sizeof(tx_silence), &written, 100);
            if (active && playback_ended_.load() && uxQueueMessagesWaiting(downlink_queue_) == 0) {
                playback_active_.store(false);
                playback_started_.store(false);
                ESP_LOGI(kTag, "playback stats: received=%lu played=%lu dropped=%lu underruns=%lu",
                         static_cast<unsigned long>(downlink_received_.load()),
                         static_cast<unsigned long>(playback_frames_.load()),
                         static_cast<unsigned long>(downlink_dropped_.load()),
                         static_cast<unsigned long>(playback_underruns_.load()));
                app::post_event(app::EventType::PlaybackFinished, current_session_.load());
            }
        }
    }
}

void AudioEngine::flush_queue(QueueHandle_t queue, QueueHandle_t free_queue)
{
    if (!queue || !free_queue) return;
    AudioFrame *frame = nullptr;
    while (xQueueReceive(queue, &frame, 0) == pdPASS) {
        xQueueSend(free_queue, &frame, 0);
    }
}

void AudioEngine::report_audio_error(uint32_t error)
{
    const uint32_t now = now_ms();
    if (now - last_error_log_ms_ > 1000) {
        last_error_log_ms_ = now;
        ESP_LOGW(kTag, "I2S transfer error: 0x%lx", static_cast<unsigned long>(error));
        app::post_event(app::EventType::AudioError, current_session_.load(), error);
    }
}
