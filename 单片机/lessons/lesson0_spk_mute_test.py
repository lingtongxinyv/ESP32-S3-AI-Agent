# ============================================================
# L0 音频诊断脚本：只测功放，排除 OLED/麦克风/计算干扰
# 目的：判断杂音来自哪里
# 现象对照：
#   情况A → 全程安静，按按钮才出哔声 → 功放+I2S配置没问题，杂音来自 lesson5 的其他因素
#   情况B → 一开始就有杂音，哪怕持续输出全零静音 → MAX98357A SD引脚硬拉上(功放永远开)，
#           静音数据没生效，或者 I2S DIN 线悬空噪声被放大
#
# 接线：只接功放的 VCC(5V)/GND/BCLK(GPIO15)/LRCLK(GPIO16)/DIN(GPIO17)，其他全不接！
# 运行：python -m mpremote connect COM11 run lessons/lesson0_spk_mute_test.py
# ============================================================
from machine import I2S, Pin
import time, math

RATE = 16000
btn = Pin(4, Pin.IN, Pin.PULL_UP)   # 按键：GPIO4

print("=" * 50)
print("L0 SPK MUTE TEST — 只接功放，其他全断开")
print("=" * 50)

# ---- 初始化功放（TX，STEREO，16bit，16kHz）-------------------------------
spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
          mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)
print("功放已初始化")

# ---- 1. 持续输出静音数据（全零）------------------------------------------
# 如果 MAX98357A 收到稳定的 0 数据，理论上不会有可闻噪声
# 如果这一步仍有杂音 → 功放一直开着 + DIN 上有悬空噪声
print("阶段1: 持续输出静音数据(全零) —— 听喇叭有没有杂音")
print("  (等 3 秒后自动进入阶段2)")
silence = bytearray(RATE * 4)    # 1 秒全零静音，STEREO 4 字节/采样

t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 3000:
    spk.write(silence)          # 循环写静音
print("  3秒到")

# ---- 2. 440Hz 正弦哔声 1 秒 ---------------------------------------------
print("阶段2: 播放 440Hz 正弦 1 秒 —— 听有没有清晰哔声")
tone = bytearray(RATE * 4)       # STEREO 4 字节/采样
for i in range(RATE):
    v = int(12000 * math.sin(2 * math.pi * 440 * i / RATE))
    lo = v & 0xFF; hi = (v >> 8) & 0xFF
    s = i * 4
    tone[s]   = lo;  tone[s+1] = hi    # 左声道
    tone[s+2] = lo;  tone[s+3] = hi    # 右声道
spk.write(tone)
print("  哔声结束")

# ---- 3. 再回到静音 3 秒 -------------------------------------------------
print("阶段3: 再输出 3 秒静音 —— 回到安静了吗？")
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 3000:
    spk.write(silence)

# ---- 4. 按键循环测试 ----------------------------------------------------
print()
print("阶段4: 按键循环 —— 每次按键切换：哔 1 秒 → 静音 2 秒")
print("  按 Ctrl+C 退出")
print()

while True:
    print("> 静音中...按按钮哔一声")
    # 静音 + 等按键
    start = time.ticks_ms()
    while btn.value() == 1:
        spk.write(silence[:640])   # 小块循环静音，每块 20ms
        if time.ticks_diff(time.ticks_ms(), start) > 15000:
            # 15 秒没按键，自动退出
            print("  (15秒无操作, 自动退出)")
            break
    if time.ticks_diff(time.ticks_ms(), start) > 15000:
        break
    while btn.value() == 0:
        time.sleep_ms(10)

    # 哔 1 秒
    print("> 哔!")
    spk.write(tone)
    time.sleep_ms(500)

    # 再静音
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < 2000:
        spk.write(silence[:640])
        if btn.value() == 0:
            time.sleep_ms(30)
            break
    while btn.value() == 0:
        time.sleep_ms(10)

# 清理
spk.deinit()
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print("清理完毕，退出")
