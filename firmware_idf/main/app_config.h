#pragma once

#include <cstddef>
#include <cstdint>

namespace config {
// 1.0.1: 修复 66s WSS 重连循环（first_ws_connected_ 守卫）；
//        设备令牌改走 Authorization 头（后发现 cpolar WS Upgrade 剥头，1.0.2 改走子协议）。
// 1.0.2: WS 凭证改走 Sec-WebSocket-Protocol: bearer.<token>（cpolar 实测透传此头）；
//        HTTP/OTA 仍走 Authorization（cpolar 不剥普通 HTTP 的头）。
// 1.0.3: 66s bug 真正根因修复——ERROR 恢复路径（L155）不再无条件重置 next_ota_check_ms_。
//        之前只修了 WsConnected 分支（L231），但 WSS 断开 → ERROR → 恢复时 L155 会覆盖 6h 为 60s。
// 1.0.4: 修复同步 I2C 模式下 OLED 只显示首帧；下行播放预缓冲从 160ms 增至 1s，减少公网抖动卡顿。
// 1.0.5: 增加下行接收、丢帧、播放与欠载统计，用于定位公网音频卡顿。
// 1.0.6: 下行帧池从 96 扩至 2048（PSRAM 约 1.3MB），避免整句 TTS 瞬时下发时丢帧。
// 1.0.7: 支持小綾按轮次下发临时情绪，八种 OLED 表情改为本地 4fps 动画；手动表情仍持久化并优先。
// 1.0.8: 修复同步 I2C 完成回调与 blit 重复释放信号量导致的显示重入；改用互斥锁保护动画任务。
// 1.0.9: 动画改由控制任务按 250ms 驱动，取消独立显示任务及其额外栈，缩小启动并发与内存压力。
// 1.0.10: SNTP 校时后固定使用北京时间（UTC+8、无夏令时），并记录校准后的本地时间。
// 1.0.11: 修复 HTTPS OTA 提前退出 perform 循环，确保 finish 切换启动分区。
// 1.0.12: 修复版真实 OTA 回归目标。
// 1.0.13: 已绑定设备可通过 BLE 授权改网并保留身份；配置包保存唤醒词并返回 config_ack。
// 1.0.14: 真机远程 OTA 回归目标，验证启动槽切换、自检与模型分区保留。
inline constexpr char kFirmwareVersion[] = "1.0.13";
inline constexpr uint8_t kProtocolVersion = 1;

inline constexpr int kOledSda = 11;
inline constexpr int kOledScl = 12;
inline constexpr int kButtonGpio = 0;  // BOOT 键（开发板自带，官方例程用 GPIO 0 作按键）
inline constexpr int kMicBclk = 5;
inline constexpr int kMicWs = 6;
inline constexpr int kMicData = 7;
inline constexpr int kSpeakerBclk = 15;
inline constexpr int kSpeakerWs = 16;
inline constexpr int kSpeakerData = 17;

inline constexpr uint32_t kSampleRate = 16000;
inline constexpr uint32_t kFrameMs = 20;
inline constexpr size_t kSamplesPerFrame = kSampleRate * kFrameMs / 1000;
inline constexpr size_t kMonoFrameBytes = kSamplesPerFrame * sizeof(int16_t);
inline constexpr size_t kMicRawFrameBytes = kSamplesPerFrame * sizeof(int32_t);
inline constexpr size_t kStereoFrameBytes = kSamplesPerFrame * 2 * sizeof(int16_t);
inline constexpr size_t kJitterFrames = 60; // 1.2 s，覆盖实测两句之间约 160 ms 的欠载
inline constexpr size_t kDownlinkFrames = 2048; // 40.96 s，PSRAM 约 1.3 MB
inline constexpr size_t kUplinkFrames = 32;

inline constexpr uint32_t kVadThreshold = 200;
inline constexpr uint32_t kVadMinSpeechMs = 160;
inline constexpr uint32_t kVadSilenceMs = 450;
inline constexpr uint32_t kMaxRecordingMs = 15000;
inline constexpr uint32_t kButtonLongPressMs = 3000;
inline constexpr uint32_t kButtonDebounceMs = 35;

inline constexpr size_t kBinaryHeaderBytes = 16;
inline constexpr size_t kMaxJsonMessage = 8192;
inline constexpr size_t kMaxHttpResponse = 16384;
} // namespace config
