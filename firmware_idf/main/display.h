#pragma once

#include "app_control.h"
#include "driver/i2c_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

class Display {
public:
    esp_err_t init();
    void show(app::State state, const char *text = nullptr);
    /** 手动画面：表情 + 一行文字，由控制层负责 NVS 持久化。 */
    void show_face(const char *emotion, const char *text = nullptr);
    /** 当前语音轮次的临时表情，不覆盖手动画面缓存。 */
    void show_auto_face(const char *emotion);
    void clear_auto_face();
    /** 撤掉手动与临时画面，下一次 show() 恢复状态界面。 */
    void clear_face();
    /** 由控制任务周期调用；不在显示模块另建任务。 */
    void animation_tick();

private:
    /** 调用方必须持有 transfer_done_；临时表情优先于手动表情。 */
    void draw_current_face(uint32_t frame);
    void draw_text(int x, int y, const char *text, size_t max_chars);
    void draw_face(const char *emotion, const char *text, uint32_t frame);

    // 点阵绘图原语：面板型号未定，表情全部按 128x64 坐标画，换屏只改这一层。
    void pixel(int x, int y);
    void fill_rect(int x, int y, int width, int height);
    void fill_circle(int cx, int cy, int radius);
    void draw_line(int x0, int y0, int x1, int y1, int thickness);
    void draw_curve(int x0, int y0, int cx, int cy, int x1, int y1, int thickness);
    void blit();

    i2c_master_bus_handle_t bus_ = nullptr;
    esp_lcd_panel_io_handle_t io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    SemaphoreHandle_t transfer_done_ = nullptr;
    uint8_t pixels_[128 * 64 / 8] = {};
    char face_emotion_[16] = {};
    char face_text_[32] = {};
    char auto_emotion_[16] = {};
    uint32_t animation_frame_ = 0;
    TickType_t last_animation_tick_ = 0;
    bool ready_ = false;
    bool face_active_ = false;
    bool auto_face_active_ = false;
};
