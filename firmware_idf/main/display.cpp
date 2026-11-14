#include "display.h"

#include <cctype>
#include <cstdio>
#include <cstring>
#include "app_config.h"
#include "esp_lcd_panel_ssd1306.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "display";

const uint8_t *glyph(char c)
{
    static constexpr uint8_t font[][5] = {
        {0x00,0x00,0x00,0x00,0x00}, {0x00,0x00,0x5f,0x00,0x00},
        {0x00,0x07,0x00,0x07,0x00}, {0x14,0x7f,0x14,0x7f,0x14},
        {0x24,0x2a,0x7f,0x2a,0x12}, {0x23,0x13,0x08,0x64,0x62},
        {0x36,0x49,0x55,0x22,0x50}, {0x00,0x05,0x03,0x00,0x00},
        {0x00,0x1c,0x22,0x41,0x00}, {0x00,0x41,0x22,0x1c,0x00},
        {0x14,0x08,0x3e,0x08,0x14}, {0x08,0x08,0x3e,0x08,0x08},
        {0x00,0x50,0x30,0x00,0x00}, {0x08,0x08,0x08,0x08,0x08},
        {0x00,0x60,0x60,0x00,0x00}, {0x20,0x10,0x08,0x04,0x02},
        {0x3e,0x51,0x49,0x45,0x3e}, {0x00,0x42,0x7f,0x40,0x00},
        {0x42,0x61,0x51,0x49,0x46}, {0x21,0x41,0x45,0x4b,0x31},
        {0x18,0x14,0x12,0x7f,0x10}, {0x27,0x45,0x45,0x45,0x39},
        {0x3c,0x4a,0x49,0x49,0x30}, {0x01,0x71,0x09,0x05,0x03},
        {0x36,0x49,0x49,0x49,0x36}, {0x06,0x49,0x49,0x29,0x1e},
        {0x00,0x36,0x36,0x00,0x00}, {0x00,0x56,0x36,0x00,0x00},
        {0x08,0x14,0x22,0x41,0x00}, {0x14,0x14,0x14,0x14,0x14},
        {0x00,0x41,0x22,0x14,0x08}, {0x02,0x01,0x51,0x09,0x06},
        {0x32,0x49,0x79,0x41,0x3e}, {0x7e,0x11,0x11,0x11,0x7e},
        {0x7f,0x49,0x49,0x49,0x36}, {0x3e,0x41,0x41,0x41,0x22},
        {0x7f,0x41,0x41,0x22,0x1c}, {0x7f,0x49,0x49,0x49,0x41},
        {0x7f,0x09,0x09,0x09,0x01}, {0x3e,0x41,0x49,0x49,0x7a},
        {0x7f,0x08,0x08,0x08,0x7f}, {0x00,0x41,0x7f,0x41,0x00},
        {0x20,0x40,0x41,0x3f,0x01}, {0x7f,0x08,0x14,0x22,0x41},
        {0x7f,0x40,0x40,0x40,0x40}, {0x7f,0x02,0x0c,0x02,0x7f},
        {0x7f,0x04,0x08,0x10,0x7f}, {0x3e,0x41,0x41,0x41,0x3e},
        {0x7f,0x09,0x09,0x09,0x06}, {0x3e,0x41,0x51,0x21,0x5e},
        {0x7f,0x09,0x19,0x29,0x46}, {0x46,0x49,0x49,0x49,0x31},
        {0x01,0x01,0x7f,0x01,0x01}, {0x3f,0x40,0x40,0x40,0x3f},
        {0x1f,0x20,0x40,0x20,0x1f}, {0x3f,0x40,0x38,0x40,0x3f},
        {0x63,0x14,0x08,0x14,0x63}, {0x07,0x08,0x70,0x08,0x07},
        {0x61,0x51,0x49,0x45,0x43},
    };
    const unsigned char uc = static_cast<unsigned char>(c);
    if (uc >= 'a' && uc <= 'z') c = static_cast<char>(uc - 'a' + 'A');
    if (c < 32 || c > 90) c = '?';
    return font[c - 32];
}
} // namespace

esp_err_t Display::init()
{
    // 多任务共用一个 framebuffer；互斥锁同时提供优先级继承。
    transfer_done_ = xSemaphoreCreateMutex();
    if (!transfer_done_) return ESP_ERR_NO_MEM;

    i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = static_cast<gpio_num_t>(config::kOledSda),
        .scl_io_num = static_cast<gpio_num_t>(config::kOledScl),
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .intr_priority = 0,
        // trans_queue_depth > 0 会开启异步 I2C，此时总线错误无法上报，
        // 且 SSD1306 初始化会静默失败（屏幕不亮）。固定用同步模式。
        .trans_queue_depth = 0,
        .flags = {.enable_internal_pullup = true},
    };
    esp_err_t err = i2c_new_master_bus(&bus_config, &bus_);
    if (err != ESP_OK) return err;

    esp_lcd_panel_io_i2c_config_t io_config = {
        .dev_addr = 0x3c,
        .scl_speed_hz = 100000,
        .control_phase_bytes = 1,
        .dc_bit_offset = 6,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .on_color_trans_done = nullptr,
        .user_ctx = this,
        .flags = {},
        .transaction_timeout_ms = 100,
    };
    err = esp_lcd_new_panel_io_i2c(bus_, &io_config, &io_);
    if (err != ESP_OK) return err;
    // 同步 I2C 也会执行 on_color_trans_done；不注册回调，统一由 blit() 释放互斥锁。

    esp_lcd_panel_ssd1306_config_t vendor_config = {.height = 64, .contrast = 128};
    esp_lcd_panel_dev_config_t panel_config = {
        .bits_per_pixel = 1,
        .reset_gpio_num = GPIO_NUM_NC,
        .vendor_config = &vendor_config,
        .flags = {},
    };
    err = esp_lcd_new_panel_ssd1306(io_, &panel_config, &panel_);
    if (err != ESP_OK) return err;
    if ((err = esp_lcd_panel_reset(panel_)) != ESP_OK) return err;
    if ((err = esp_lcd_panel_init(panel_)) != ESP_OK) return err;
    // 面板相对机壳是倒装的（180°），X/Y 都要翻转，否则画面上下颠倒、文字左右镜像。
    if ((err = esp_lcd_panel_mirror(panel_, true, true)) != ESP_OK) return err;
    if ((err = esp_lcd_panel_disp_on_off(panel_, true)) != ESP_OK) return err;
    ready_ = true;
    show(app::State::UNPROVISIONED, "UART0 SETUP");
    // 动画由既有控制任务调用 animation_tick()，避免启动阶段新增任务栈和显示并发。
    return ESP_OK;
}

void Display::show(app::State state, const char *text)
{
    if (!ready_ || xSemaphoreTake(transfer_done_, pdMS_TO_TICKS(150)) != pdPASS) return;
    // 语音状态切换只更新顶部状态栏，不再用整页状态文字覆盖常驻/自动表情。
    const bool voice_state = state == app::State::IDLE || state == app::State::LISTENING ||
                             state == app::State::THINKING || state == app::State::SPEAKING;
    if (voice_state && (auto_face_active_ || face_active_)) {
        draw_current_face(0);
        blit();
        return;
    }
    std::memset(pixels_, 0, sizeof(pixels_));
    draw_text(0, 0, "ESP32-S3 AI AGENT", 21);
    draw_text(0, 8, app::state_name(state), 21);
    const char *hint = text;
    switch (state) {
        // 语音页只显示固定状态，不把 ASR/LLM 中文正文逐 token 刷到 OLED。
        case app::State::IDLE: hint = nullptr; break;
        case app::State::LISTENING: hint = "SPEAK NOW"; break;
        case app::State::THINKING: hint = "PLEASE WAIT"; break;
        case app::State::SPEAKING: hint = "REPLY PLAYING"; break;
        case app::State::OTA:
            if (!hint || !*hint) hint = "DO NOT POWER OFF";
            break;
        case app::State::CONNECTING:
            if (!hint || !*hint) hint = "CONNECTING";
            break;
        case app::State::UNPROVISIONED:
            if (!hint || !*hint) hint = "BLE / UART0 SETUP";
            break;
        default: break;
    }
    if (hint && *hint) {
        const size_t length = std::strlen(hint);
        size_t offset = length > 84 ? length - 84 : 0;
        for (size_t line = 0; line < 4; ++line) {
            char row[22] = {};
            size_t remaining = length > offset + line * 21 ? length - offset - line * 21 : 0;
            if (!remaining) break;
            size_t count = remaining > 21 ? 21 : remaining;
            for (size_t i = 0; i < count; ++i) {
                const unsigned char c = hint[offset + line * 21 + i];
                row[i] = c >= 32 && c < 127 ? static_cast<char>(c) : '?';
            }
            draw_text(0, 24 + line * 10, row, 21);
        }
    }
    blit();
}

void Display::show_face(const char *emotion, const char *text)
{
    if (!ready_ || xSemaphoreTake(transfer_done_, pdMS_TO_TICKS(150)) != pdPASS) return;
    face_active_ = true;
    auto_face_active_ = false;
    std::snprintf(face_emotion_, sizeof(face_emotion_), "%s", emotion && *emotion ? emotion : "neutral");
    std::snprintf(face_text_, sizeof(face_text_), "%s", text ? text : "");
    draw_current_face(0);
    blit();
}

void Display::show_auto_face(const char *emotion)
{
    if (!ready_ || xSemaphoreTake(transfer_done_, pdMS_TO_TICKS(150)) != pdPASS) return;
    auto_face_active_ = true;
    std::snprintf(auto_emotion_, sizeof(auto_emotion_), "%s", emotion && *emotion ? emotion : "neutral");
    draw_current_face(0);
    blit();
}

void Display::clear_auto_face()
{
    if (!ready_ || xSemaphoreTake(transfer_done_, pdMS_TO_TICKS(150)) != pdPASS) return;
    auto_face_active_ = false;
    xSemaphoreGive(transfer_done_);
}

void Display::clear_face()
{
    if (!ready_ || xSemaphoreTake(transfer_done_, pdMS_TO_TICKS(150)) != pdPASS) return;
    face_active_ = false;
    auto_face_active_ = false;
    xSemaphoreGive(transfer_done_);
}

void Display::draw_current_face(uint32_t frame)
{
    std::memset(pixels_, 0, sizeof(pixels_));
    if (auto_face_active_) {
        draw_face(auto_emotion_, "", frame);
    } else {
        draw_face(face_emotion_, face_text_, frame);
    }
    const app::State current = app::state();
    if (current != app::State::IDLE) {
        // 临时表情出现时仍保留状态栏，避免回复阶段看起来又退回待机。
        std::memset(pixels_, 0, 128);
        draw_text(0, 0, app::state_name(current), 21);
    }
}

void Display::animation_tick()
{
    if (!ready_ || app::state() == app::State::ERROR || (!auto_face_active_ && !face_active_)) return;
    const TickType_t now = xTaskGetTickCount();
    if (now - last_animation_tick_ < pdMS_TO_TICKS(250)) return;
    last_animation_tick_ = now;
    if (xSemaphoreTake(transfer_done_, pdMS_TO_TICKS(150)) != pdPASS) return;
    draw_current_face(animation_frame_++);
    blit();
}

void Display::blit()
{
    esp_err_t err = esp_lcd_panel_draw_bitmap(panel_, 0, 0, 128, 64, pixels_);
    // 总线固定为同步模式，draw_bitmap 返回时传输已经结束；不能依赖异步回调释放信号量。
    xSemaphoreGive(transfer_done_);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "OLED update failed: %s", esp_err_to_name(err));
    }
}

void Display::draw_text(int x, int y, const char *text, size_t max_chars)
{
    for (size_t index = 0; index < max_chars && text[index]; ++index) {
        const uint8_t *columns = glyph(text[index]);
        for (int column = 0; column < 5; ++column) {
            for (int row = 0; row < 7; ++row) {
                if ((columns[column] & (1 << row)) && x + column < 128 && y + row < 64) {
                    const int px = x + index * 6 + column;
                    const int py = y + row;
                    pixels_[(py / 8) * 128 + px] |= 1 << (py % 8);
                }
            }
        }
    }
}

void Display::pixel(int x, int y)
{
    if (x < 0 || x >= 128 || y < 0 || y >= 64) return;
    pixels_[(y / 8) * 128 + x] |= static_cast<uint8_t>(1u << (y % 8));
}

void Display::fill_rect(int x, int y, int width, int height)
{
    for (int row = 0; row < height; ++row) {
        for (int column = 0; column < width; ++column) {
            pixel(x + column, y + row);
        }
    }
}

void Display::fill_circle(int cx, int cy, int radius)
{
    const int limit = radius * radius;
    for (int dy = -radius; dy <= radius; ++dy) {
        for (int dx = -radius; dx <= radius; ++dx) {
            if (dx * dx + dy * dy <= limit) pixel(cx + dx, cy + dy);
        }
    }
}

void Display::draw_line(int x0, int y0, int x1, int y1, int thickness)
{
    const int dx = x1 > x0 ? x1 - x0 : x0 - x1;
    const int sx = x0 < x1 ? 1 : -1;
    const int dy = y1 > y0 ? y0 - y1 : y1 - y0;
    const int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    for (;;) {
        if (thickness <= 1) {
            pixel(x0, y0);
        } else {
            fill_circle(x0, y0, thickness / 2);
        }
        if (x0 == x1 && y0 == y1) break;
        const int e2 = 2 * err;
        if (e2 >= dy) {
            err += dy;
            x0 += sx;
        }
        if (e2 <= dx) {
            err += dx;
            y0 += sy;
        }
    }
}

void Display::draw_curve(int x0, int y0, int cx, int cy, int x1, int y1, int thickness)
{
    for (int step = 0; step <= 32; ++step) {
        const float t = static_cast<float>(step) / 32.0f;
        const float mt = 1.0f - t;
        const int x = static_cast<int>(mt * mt * x0 + 2.0f * mt * t * cx + t * t * x1 + 0.5f);
        const int y = static_cast<int>(mt * mt * y0 + 2.0f * mt * t * cy + t * t * y1 + 0.5f);
        if (thickness <= 1) {
            pixel(x, y);
        } else {
            fill_circle(x, y, thickness / 2);
        }
    }
}

/**
 * 表情画面：左眼、右眼、嘴三块按情绪换形。
 * 换面板型号时只需重写这一个函数，协议里的 emotion 字符串保持不变。
 */
void Display::draw_face(const char *emotion, const char *text, uint32_t frame)
{
    constexpr int kLeftEyeX = 42;
    constexpr int kRightEyeX = 86;
    constexpr int kEyeY = 24;
    constexpr int kMouthY = 46;

    const bool happy = std::strcmp(emotion, "happy") == 0;
    const bool sad = std::strcmp(emotion, "sad") == 0;
    const bool angry = std::strcmp(emotion, "angry") == 0;
    const bool surprised = std::strcmp(emotion, "surprised") == 0;
    const bool thinking = std::strcmp(emotion, "thinking") == 0;
    const bool sleepy = std::strcmp(emotion, "sleepy") == 0;
    const bool love = std::strcmp(emotion, "love") == 0;
    const bool blink = frame % 16 == 15;

    if (happy) {
        // 每四秒眨眼一次；其余时间保持 ^ ^ 笑眼。
        if (blink) {
            draw_line(kLeftEyeX - 8, kEyeY + 2, kLeftEyeX + 8, kEyeY + 2, 2);
            draw_line(kRightEyeX - 8, kEyeY + 2, kRightEyeX + 8, kEyeY + 2, 2);
        } else {
            draw_curve(kLeftEyeX - 9, kEyeY + 5, kLeftEyeX, kEyeY - 8, kLeftEyeX + 9, kEyeY + 5, 3);
            draw_curve(kRightEyeX - 9, kEyeY + 5, kRightEyeX, kEyeY - 8, kRightEyeX + 9, kEyeY + 5, 3);
        }
        draw_curve(50, kMouthY - 3, 64, kMouthY + 10, 78, kMouthY - 3, 3);
    } else if (sad) {
        draw_line(32, 12, 50, 19, 2);
        draw_line(96, 12, 78, 19, 2);
        fill_circle(kLeftEyeX, kEyeY, 6);
        fill_circle(kRightEyeX, kEyeY, 6);
        draw_curve(52, kMouthY + 6, 64, kMouthY - 4, 76, kMouthY + 6, 3);
        fill_circle(34, 32 + (frame % 4) * 4, 2);
    } else if (angry) {
        const int left_jitter = frame % 2;
        draw_line(32, 15 + left_jitter, 50, 24 + left_jitter, 3);
        draw_line(96, 16 - left_jitter, 78, 25 - left_jitter, 3);
        fill_circle(kLeftEyeX, kEyeY + 2, 4);
        fill_circle(kRightEyeX, kEyeY + 2, 4);
        draw_curve(52, kMouthY + 6, 64, kMouthY - 2, 76, kMouthY + 6, 3);
    } else if (surprised) {
        const int expansion = (frame % 4 == 1 || frame % 4 == 2) ? 2 : 0;
        fill_circle(kLeftEyeX, kEyeY, 8 + expansion);
        fill_circle(kRightEyeX, kEyeY, 8 + expansion);
        fill_circle(64, kMouthY + 1, 4 + expansion);
    } else if (thinking) {
        fill_circle(kLeftEyeX, kEyeY, 7);
        fill_circle(kRightEyeX, kEyeY, 3);
        draw_line(56, kMouthY + 2, 72, kMouthY - 2, 2);
        for (int dot = 0; dot < 3; ++dot) {
            fill_circle(96 + dot * 8, 14 - dot * 3, dot == static_cast<int>(frame % 3) ? 2 : 1);
        }
    } else if (sleepy) {
        draw_line(32, kEyeY, 52, kEyeY, 2);
        draw_line(76, kEyeY, 96, kEyeY, 2);
        draw_line(58, kMouthY + 2, 70, kMouthY + 2, 2);
        draw_text(100 + (frame % 2) * 4, 14 - (frame % 4) * 3, "Z", 1);
    } else if (love) {
        const int pulse = (frame % 4 == 1 || frame % 4 == 2) ? 1 : 0;
        const int eye_centers[2] = {kLeftEyeX, kRightEyeX};
        for (int cx : eye_centers) {
            fill_circle(cx - 3, kEyeY - 3, 4 + pulse);
            fill_circle(cx + 3, kEyeY - 3, 4 + pulse);
            fill_rect(cx - 6 - pulse, kEyeY - 1, 13 + pulse * 2, 3);
            draw_line(cx - 6 - pulse, kEyeY + 1, cx, kEyeY + 8 + pulse, 2);
            draw_line(cx + 6 + pulse, kEyeY + 1, cx, kEyeY + 8 + pulse, 2);
        }
        draw_curve(52, kMouthY - 2, 64, kMouthY + 8, 76, kMouthY - 2, 3);
    } else {
        if (blink) {
            draw_line(kLeftEyeX - 7, kEyeY, kLeftEyeX + 7, kEyeY, 2);
            draw_line(kRightEyeX - 7, kEyeY, kRightEyeX + 7, kEyeY, 2);
        } else {
            fill_circle(kLeftEyeX, kEyeY, 7);
            fill_circle(kRightEyeX, kEyeY, 7);
        }
        draw_line(52, kMouthY, 76, kMouthY, 2);
    }

    if (text && *text) {
        draw_text(0, 54, text, 21);
    }
}
