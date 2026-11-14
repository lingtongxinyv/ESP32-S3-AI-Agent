# L4/L5 实验：麦克风 -> 喇叭 实时直通，OLED 显示状态
# 运行：python -m mpremote connect COM11 run lessons/lesson4_passthrough.py
# 流程：先"哔"一声验证喇叭 -> 按键开始直通 -> 再按键结束
from machine import I2S, Pin, SoftI2C
import ssd1306, time, math

RATE = 16000
BITS = 16

# ---- 外设 ---------------------------------------------------------------
i2c = SoftI2C(scl=Pin(12), sda=Pin(11), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
btn = Pin(4, Pin.IN, Pin.PULL_UP)

# 实测：本模块 16 位直读即可（麦克风移出高 16 位，后面的位被截断，正好能用）
mic = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
          mode=I2S.RX, bits=BITS, format=I2S.MONO, rate=RATE, ibuf=8192)

# !!! MAX98357A 要求 BCLK 必须是 LRCLK 的 32/48/64 倍 !!!
# MONO: BCLK=256kHz (16k*16) → 不匹配 → 持续噪声
# STEREO: BCLK=512kHz (16k*2*16) = 32×LRCLK ✓
spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
          mode=I2S.TX, bits=BITS, format=I2S.STEREO, rate=RATE, ibuf=8192)

def show(lines, bar=0):
    oled.fill(0)
    oled.rect(0, 0, 128, 64, 1)
    for i, s in enumerate(lines):
        oled.text(s, 6, 6 + i * 16, 1)
    if bar:                                   # 底部音量条
        w = min(116, bar)
        oled.fill_rect(6, 52, w, 8, 1)
    oled.show()

def wait_press(msg):
    show([msg, "press button"])
    while btn.value() == 1:
        time.sleep_ms(10)
    while btn.value() == 0:                   # 等松手，防连触
        time.sleep_ms(10)
    time.sleep_ms(50)                         # 消抖

# ---- 1. 喇叭自检：440Hz 正弦"哔"1 秒 --------------------------------------
show(["SPK self-test", "beep 1s..."])
n = RATE  # 1 秒，16000 采样
# 生成 MONO tone，然后复制成 STEREO 给 MAX98357A
tone_mono = bytearray(n * 2)
for i in range(n):
    v = int(12000 * math.sin(2 * math.pi * 440 * i / RATE))
    tone_mono[i*2] = v & 0xFF
    tone_mono[i*2+1] = (v >> 8) & 0xFF
tone_stereo = bytearray(n * 4)
for i in range(n):
    s = i * 4
    m = i * 2
    tone_stereo[s]   = tone_mono[m];   tone_stereo[s+1] = tone_mono[m+1]  # 左
    tone_stereo[s+2] = tone_mono[m];   tone_stereo[s+3] = tone_mono[m+1]  # 右
spk.write(tone_stereo)
print("beep done")

# ---- 2. 主循环：按键开始/停止直通，可反复 --------------------------------
# 关键：主循环里只做 read + write。块 20ms，屏幕每 6 块才刷一次。
# 否则一刷屏（SoftI2C 约 25ms）就超过 20ms，FIFO 溢出 / 喇叭欠载 = 噪声。
MONO_SAMPLES = 320              # 20ms @16k
mono_chunk = bytearray(MONO_SAMPLES * 2)     # 麦克风读 MONO
stereo_chunk = bytearray(MONO_SAMPLES * 4)  # 喇叭写 STEREO

def mono2stereo(m, s, n):
    """MONO (n*2 bytes) → STEREO (n*4 bytes), 左右同值"""
    for i in range(n):
        mi = i * 2
        si = i * 4
        s[si]   = m[mi];    s[si+1] = m[mi+1]
        s[si+2] = m[mi];    s[si+3] = m[mi+1]

cnt = 0
peak = 0

while True:
    wait_press("BEEP ok? ready")
    show(["REC -> SPK", "live!", "press to stop"])
    print("passthrough start")
    cnt = 0

    while btn.value() == 1:
        mic.readinto(mono_chunk)
        mono2stereo(mono_chunk, stereo_chunk, MONO_SAMPLES)
        spk.write(stereo_chunk)

        # 音量只算个大概值，不做重活
        v = mono_chunk[0]
        if v > 127: v = 256 - v
        if v > peak: peak = v

        cnt += 1
        if cnt % 6 == 0:                     # 每约 120ms 刷一次屏
            show(["REC -> SPK", "live!", "press to stop"], bar=peak * 4)
            peak = 0

    show(["stopped", "press = again"])
    print("passthrough stop")
    while btn.value() == 0:               # 等松手
        time.sleep_ms(10)
    time.sleep_ms(50)

mic.deinit(); spk.deinit()
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
