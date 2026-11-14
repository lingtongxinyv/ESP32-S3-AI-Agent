# L5 过关版：按键触发「录 → 关麦 → 播放」
# 为什么不用实时直通？
#   ESP32-S3 双 I2S 同时初始化有硬件级干扰杂音（STEP 2 已证明）
#   但「先关后开」单 I2S 模式完全正常（lesson3/4/lesson0_7 已验证）
# 所以本版：按键 → 开麦录 2 秒 → 关麦 → 开功放放 → 回 idle
from machine import I2S, Pin, SoftI2C
import ssd1306, time, struct, gc

RATE = 16000
REC_SECONDS = 2          # 录 2 秒
N = RATE * REC_SECONDS   # 32000 samples

# ---- OLED + 按键 ---------------------------------------------------------
i2c = SoftI2C(scl=Pin(12), sda=Pin(11), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
btn = Pin(4, Pin.IN, Pin.PULL_UP)

# ---- 缓冲区（只在需要时创建）------------------------------------------
stereo_buf = None   # 存转换后的 16bit STEREO，放功放

def draw(state, bar=0):
    oled.fill(0)
    oled.rect(0, 0, 128, 64, 1)
    oled.text("L5 OK - REC/PLAY", 2, 4, 1)
    oled.hline(0, 16, 128, 1)
    if state == "idle":
        oled.text("IDLE - press = REC", 6, 28, 1)
        oled.text("listen then play", 6, 44, 1)
    elif state == "rec":
        oled.text("RECORDING...", 20, 28, 1)
        oled.fill_rect(6, 38, 8, 8, 1)
        oled.text("2 sec, speak!", 18, 38, 1)
        if bar > 0:
            w = min(116, bar)
            oled.fill_rect(6, 52, w, 8, 1)
    elif state == "play":
        oled.text("PLAYING BACK...", 18, 28, 1)
        oled.text("your voice!", 28, 44, 1)
        if bar > 0:
            w = min(116, bar)
            oled.fill_rect(6, 52, w, 8, 1)
    oled.show()

def wait_press():
    """等按下再松手"""
    while btn.value() == 1:
        time.sleep_ms(10)
    while btn.value() == 0:
        time.sleep_ms(10)
    time.sleep_ms(50)   # 消抖

# ---- 主循环 ---------------------------------------------------------------
print("=" * 50)
print("L5 过关版 — 录 2 秒 → 自动播放")
print("  单 I2S 先关后开, 无杂音")
print("=" * 50)

state = "idle"
draw(state)

while True:
    # === IDLE ===
    draw("idle")
    wait_press()

    # === REC: 开麦 → 录 → 关麦 ===
    print("-> REC", REC_SECONDS, "seconds...")
    draw("rec")
    gc.collect()

    # 麦克风初始化（仅 REC 时开）
    mic = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
              mode=I2S.RX, bits=32, format=I2S.MONO, rate=RATE, ibuf=8192)

    # 录到 32bit 原始数据
    raw32 = bytearray(N * 4)
    peak = 0
    t0 = time.ticks_ms()
    mic.readinto(raw32)
    dt = time.ticks_diff(time.ticks_ms(), t0)
    print(f"  录完 {dt}ms")

    # 算峰值
    vals = struct.unpack('<%di' % N, raw32)
    for i in range(N):
        s = vals[i] >> 16
        if abs(s) > peak: peak = abs(s)
    print(f"  峰值 peak={peak}/32767")

    # 关麦！！！
    mic.deinit()
    Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
    mic = None
    print("  麦克风已关闭 ✅")

    # === 转换：32bit → 16bit STEREO ===
    gc.collect()
    stereo_buf = bytearray(N * 4)   # STEREO 16bit
    for i in range(N):
        s = vals[i] >> 16
        if s > 32767: s -= 65536
        lo = s & 0xFF; hi = (s >> 8) & 0xFF
        base = i * 4
        stereo_buf[base]   = lo; stereo_buf[base+1] = hi
        stereo_buf[base+2] = lo; stereo_buf[base+3] = hi
    del raw32
    gc.collect()
    print("  转换完成")

    # === PLAY: 开功放 → 放 → 关功放 ===
    print("-> PLAY")
    draw("play")

    # 功放初始化（仅 PLAY 时开）
    spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
              mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)

    t0 = time.ticks_ms()
    spk.write(stereo_buf)
    dt = time.ticks_diff(time.ticks_ms(), t0)
    print(f"  播放完成 {dt}ms")

    # 关功放！！！
    spk.deinit()
    Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
    spk = None
    print("  功放已关闭 ✅")

    # 释放 buffer
    del stereo_buf
    gc.collect()

    state = "idle"
    print("-> IDLE (再按按钮继续)")
