# ============================================================
# 鹿小班 ESP32-S3 + ICS43434 + MAX98357A 音频测试代码
# 硬件：ICS43434(麦克风) + MAX98357A(功放) 合体模块
# 主控：ESP32-S3-N16R8 (DevKitC-1 克隆板)
# 固件：MicroPython (v1.24, SPIRAM Octal, Flash 16MB)
# ============================================================
#
# 【接线表】
# ┌───────────────┬──────────────┬──────────────┐
# │  模块引脚      │  接 ESP32-S3   │  说明         │
# ├───────────────┼──────────────┼──────────────┤
# │  VCC          │  5V (USB)    │  模块供电     │
# │  GND          │  GND         │  共地         │
# │  BCLK         │  GPIO 15     │  功放位时钟    │
# │  LRCLK        │  GPIO 16     │  功放左右声道  │
# │  DIN          │  GPIO 17     │  功放数据输入  │
# │  SCK          │  GPIO 5      │  麦克风位时钟  │
# │  WS           │  GPIO 6      │  麦克风字选择  │
# │  SD (模块)    │  GPIO 7      │  麦克风数据输出│
# └───────────────┴──────────────┴──────────────┘
#
# 重要说明：
# 1) 模块上的 "SD" 引脚是 ICS43434 麦克风的数据输出(Serial Data)，
#    不是 MAX98357A 功放的 SD/MODE！
# 2) 模块没有引出 MAX98357A 的 SD/MODE 和 GAIN_SLOT 引脚，
#    功放的使能和增益在板子上硬接固定。
# 3) 麦克风用 I2S0，功放用 I2S1，两个独立硬件 I2S。
# ============================================================


from machine import I2S, Pin
import time, math, gc

SAMPLE_RATE = 16000
BITS        = 16


# ============================================================
#  麦克风初始化 (ICS43434 → ESP32-S3 接收)
#  用 MONO 格式读取（模块设计为单声道输出）
# ============================================================
def mic_open():
    return I2S(
        0,                    # I2S0 硬件
        sck  = Pin(5),        # BCLK / SCK
        ws   = Pin(6),        # LRCLK / WS
        sd   = Pin(7),        # SD = 麦克风数据输出
        mode = I2S.RX,        # 接收方向
        bits = BITS,          # 16-bit 采样
        format = I2S.MONO,    # 单声道
        rate = SAMPLE_RATE,   # 16kHz
        ibuf = 8192,
    )


# ============================================================
#  功放初始化 (ESP32-S3 → MAX98357A 输出)
#  !!!!! 必须用 STEREO !!!!!
#
# MAX98357A 数据手册要求：BCCLK/LRCLK 时钟倍率必须是 32/48/64 之一
#   MONO 模式:  BCLK = 16k × 16 = 256kHz  → 256k/16k = 16 (×) 不合法
#   STEREO 模式: BCLK = 16k × 2 × 16 = 512kHz → 512k/16k = 32 (✓) 合法
#
# 结论：必须 STEREO 模式输出，MONO 数据在软件里复制到左右声道
# ============================================================
def spk_open():
    return I2S(
        1,                    # I2S1 硬件
        sck  = Pin(15),       # BCLK
        ws   = Pin(16),       # LRCLK
        sd   = Pin(17),       # DIN = 功放数据输入
        mode = I2S.TX,        # 发送方向
        bits = BITS,          # 16-bit
        format = I2S.STEREO,  # !!!!! 必须 STEREO !!!!!
        rate = SAMPLE_RATE,   # 16kHz
        ibuf = 8192,
    )


# ============================================================
#  MONO PCM → STEREO PCM 复制
#  MAX98357A 会自动把左右平均后输出 (L/2 + R/2 模式)
# ============================================================
def mono_to_stereo(mono_bytes, stereo_bytes, n_samples):
    """
    mono_bytes:   n_samples × 2 字节 (16-bit MONO)
    stereo_bytes: n_samples × 4 字节 (16-bit STEREO, 预分配好)
    """
    for i in range(n_samples):
        mi = i * 2
        si = i * 4
        stereo_bytes[si]   = mono_bytes[mi];    stereo_bytes[si+1] = mono_bytes[mi+1]
        stereo_bytes[si+2] = mono_bytes[mi];    stereo_bytes[si+3] = mono_bytes[mi+1]


# ============================================================
# 测试 A: 纯喇叭自检 — 放 440Hz 正弦 1 秒
# ============================================================
def test_speaker_beep():
    print("=== Test A: speaker beep (440Hz, 1s) ===")
    spk = spk_open()

    n = SAMPLE_RATE           # 16000 采样 = 1 秒
    stereo_buf = bytearray(n * 4)   # STEREO 4 字节/采样

    for i in range(n):
        v = int(12000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE))
        lo = v & 0xFF; hi = (v >> 8) & 0xFF
        base = i * 4
        stereo_buf[base]   = lo;  stereo_buf[base+1] = hi   # 左
        stereo_buf[base+2] = lo;  stereo_buf[base+3] = hi   # 右

    t0 = time.ticks_ms()
    spk.write(stereo_buf)
    dt = time.ticks_diff(time.ticks_ms(), t0)

    spk.deinit()
    # 引脚设回输入，避免悬空干扰
    Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)

    print("write done in %d ms (expect ~1000)" % dt)
    print("=> 听: 清晰 440Hz 哔声? 还是噪声?")


# ============================================================
# 测试 B: 麦克风录 3 秒 → 直接播
# ============================================================
def test_record_and_play():
    print("=== Test B: record 3s → play back ===")

    # --- 录音 ---
    gc.collect()
    mic = mic_open()
    n = SAMPLE_RATE * 3           # 48000 采样 = 3 秒
    mono_buf = bytearray(n * 2)   # 96000 字节

    print("Recording 3 seconds... speak now!")
    t0 = time.ticks_ms()
    mic.readinto(mono_buf)
    dt = time.ticks_diff(time.ticks_ms(), t0)
    mic.deinit()

    # 算峰值判断有没有录到东西
    peak = 0
    for i in range(0, len(mono_buf), 2):
        v = mono_buf[i] if mono_buf[i] < 128 else mono_buf[i] - 256
        if abs(v) > peak: peak = abs(v)

    print("Recorded %d samples in %d ms (expect 3000)" % (n, dt))
    print("Peak amplitude = %d / 32767" % peak)
    if peak < 100:
        print("  !! Peak too low → 麦克风没录到声音，检查接线/供电")

    # --- MONO → STEREO ---
    print("Converting MONO → STEREO...")
    stereo_buf = bytearray(n * 4)
    mono_to_stereo(mono_buf, stereo_buf, n)

    # --- 播放 ---
    print("Playing back...")
    gc.collect()
    spk = spk_open()
    t0 = time.ticks_ms()
    spk.write(stereo_buf)
    dt = time.ticks_diff(time.ticks_ms(), t0)

    spk.deinit()
    Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)

    print("Playback done in %d ms" % dt)
    print("=> 听: 清晰你的声音? 还是噪声?")


# ============================================================
# 测试 C: 实时直通 (麦克风 → 喇叭)，跑 5 秒自动停
# ============================================================
def test_passthrough():
    print("=== Test C: mic -> speaker passthrough (5s) ===")
    mic = mic_open()
    spk = spk_open()

    # 20ms 一块
    MONO_SAMPLES = 320
    mono_chunk   = bytearray(MONO_SAMPLES * 2)
    stereo_chunk = bytearray(MONO_SAMPLES * 4)

    total_frames = 5000 // 20   # 5 秒 = 250 块
    peak = 0
    t0 = time.ticks_ms()

    for i in range(total_frames):
        mic.readinto(mono_chunk)
        mono_to_stereo(mono_chunk, stereo_chunk, MONO_SAMPLES)
        spk.write(stereo_chunk)

        v = mono_chunk[0]
        if v > 127: v = 256 - v
        if v > peak: peak = v

    dt = time.ticks_diff(time.ticks_ms(), t0)
    mic.deinit()
    spk.deinit()
    Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)

    print("Passthrough done in %d ms (expect ~5000)" % dt)
    print("Peak = %d" % peak)
    print("=> 听: 实时清晰? 延迟? 噪声?")


# ============================================================
# 自动跑三个测试
# ============================================================
def main():
    print("=" * 50)
    print("ESP32-S3 + ICS43434 + MAX98357A 音频诊断")
    print("=" * 50)

    test_speaker_beep()
    time.sleep_ms(1500)

    test_record_and_play()
    time.sleep_ms(1500)

    test_passthrough()

    print("\n=== 全部测试完成 ===")


if __name__ == "__main__":
    main()
