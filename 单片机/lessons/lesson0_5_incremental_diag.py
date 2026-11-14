# ============================================================
# L0.5 逐步叠加诊断：定位杂音来源
# 每次只加一个模块，运行 5 秒听有没有杂音
# 听出杂音 → 这个模块就是嫌疑犯
# 没杂音 → 继续加下一个
#
# 接线：全部模块都接上（功放+麦克风+OLED+按键）
# ============================================================
from machine import I2S, Pin, SoftI2C
import ssd1306, time, struct

RATE = 16000
BITS_MIC = 32    # 麦克风 24bit 左对齐 → 用 32bit 读
BITS_SPK = 16    # 功放 16bit

def step(title, body_fn, duration_ms=5000):
    """跑一段测试 body_fn，duration_ms 毫秒，听喇叭"""
    print()
    print("=" * 50)
    print(f"STEP: {title}")
    print(f"  接线要求: 全接上")
    print(f"  听喇叭: {duration_ms//1000}秒内有没有杂音")
    print("  观察中...")
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < duration_ms:
        body_fn()
    print("  完成。有杂音就按 Ctrl+C 停，告诉我，没杂音继续下一个")
    print("=" * 50)
    time.sleep_ms(500)

# ========== STEP 1: 只有功放输出静音（基准，应该安静） ==========
def step1_body():
    pass   # 后面初始化完再说

# ========== STEP 2: 功放 + 麦克风同时开，麦克风数据丢掉 ==========
# 如果这步出杂音 → I2S0 RX 和 I2S1 TX 有冲突 / 电源不稳
mic2 = None
def step2_body():
    raw = bytearray(1280)
    mic2.readinto(raw)

# ========== STEP 3: 功放 + 麦克风 + 数据转换 ==========
# 麦克风数据 → 32bit 转 16bit → 复制 STEREO → 写功放
# 如果这步出杂音 → 转换逻辑阻塞了 I2S 写入节奏
mic3 = None
raw32 = None
stereo16 = None

def step3_body():
    global raw32, stereo16
    mic3.readinto(raw32)
    # 32bit → 16bit → STEREO（和 lesson5 一样的逻辑）
    vals = struct.unpack('<%di' % 320, raw32)
    for i in range(320):
        s = vals[i] >> 16
        lo = s & 0xFF; hi = (s >> 8) & 0xFF
        base = i * 4
        stereo16[base]   = lo; stereo16[base+1] = hi
        stereo16[base+2] = lo; stereo16[base+3] = hi
    spk3.write(stereo16)

# ========== STEP 4: 功放 + 麦克风 + 转换 + OLED (每 120ms 刷一次) ==========
# 如果这步出杂音 → SoftI2C 刷屏阻塞了 CPU / 抢了总线
oled4 = None
mic4 = None
spk4 = None
raw32_4 = None
stereo16_4 = None
cnt4 = 0

def step4_body():
    global cnt4
    mic4.readinto(raw32_4)
    vals = struct.unpack('<%di' % 320, raw32_4)
    for i in range(320):
        s = vals[i] >> 16
        lo = s & 0xFF; hi = (s >> 8) & 0xFF
        base = i * 4
        stereo16_4[base]   = lo; stereo16_4[base+1] = hi
        stereo16_4[base+2] = lo; stereo16_4[base+3] = hi
    spk4.write(stereo16_4)
    cnt4 += 1
    if cnt4 % 6 == 0:
        oled4.fill(0)
        oled4.rect(0, 0, 128, 64, 1)
        oled4.text("STEP4 OLED + ALL", 4, 4, 1)
        oled4.text("live!", 20, 28, 1)
        oled4.text("listen for noise", 6, 44, 1)
        oled4.show()
        cnt4 = 0

# ========== 主程序 ==========
print()
print("╔══════════════════════════════════════════════╗")
print("║  逐步叠加排查：找杂音来源                      ║")
print("║  每次运行 5 秒，听出杂音就 Ctrl+C 然后告诉我    ║")
print("╚══════════════════════════════════════════════╝")

# --- STEP 1: 基准 ---
print()
print(">>> STEP 1: 只有功放 + 静音（应该安静）")
spk1 = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
           mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)
silence = bytearray(1280)    # 20ms 静音块: 320 samples * 4 bytes
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 5000:
    spk1.write(silence)
spk1.deinit()
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print("  ✓ STEP 1 完成（应该全程安静）")

# --- STEP 2: 功放 + 麦克风同时开 ---
print()
print(">>> STEP 2: 功放静音 + 麦克风同时读（数据丢掉）")
print("  听: 有杂音吗？")
spk2 = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
           mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)
mic2 = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
           mode=I2S.RX, bits=BITS_MIC, format=I2S.MONO, rate=RATE, ibuf=8192)
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 5000:
    mic2.readinto(silence)      # 麦克风读，数据丢
    spk2.write(silence)          # 功放静音写
mic2.deinit(); spk2.deinit()
Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print("  ✓ STEP 2 完成")

# --- STEP 3: 功放 + 麦克风 + 数据处理 ---
print()
print(">>> STEP 3: 功放 + 麦克风 + 32→16bit + MONO→STEREO")
print("  听: 有杂音吗？")
spk3 = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
           mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)
mic3 = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
           mode=I2S.RX, bits=BITS_MIC, format=I2S.MONO, rate=RATE, ibuf=8192)
raw32 = bytearray(320 * 4)   # 20ms @16k, 32bit → 1280 字节
stereo16 = bytearray(320 * 4)  # STEREO 16bit → 1280 字节
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 5000:
    mic3.readinto(raw32)
    vals = struct.unpack('<%di' % 320, raw32)
    for i in range(320):
        s = vals[i] >> 16
        lo = s & 0xFF; hi = (s >> 8) & 0xFF
        base = i * 4
        stereo16[base]   = lo; stereo16[base+1] = hi
        stereo16[base+2] = lo; stereo16[base+3] = hi
    spk3.write(stereo16)
mic3.deinit(); spk3.deinit()
Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print("  ✓ STEP 3 完成")

# --- STEP 4: 全部模块 + OLED 刷屏 ---
print()
print(">>> STEP 4: 全部模块 + OLED 每 120ms 刷一次")
print("  听: 有杂音吗？（这就是 lesson5 的完整配置）")
spk4 = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
           mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)
mic4 = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
           mode=I2S.RX, bits=BITS_MIC, format=I2S.MONO, rate=RATE, ibuf=8192)
i2c4 = SoftI2C(scl=Pin(12), sda=Pin(11), freq=400000)
oled4 = ssd1306.SSD1306_I2C(128, 64, i2c4)
raw32_4 = bytearray(320 * 4)
stereo16_4 = bytearray(320 * 4)
cnt4 = 0
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 5000:
    mic4.readinto(raw32_4)
    vals = struct.unpack('<%di' % 320, raw32_4)
    for i in range(320):
        s = vals[i] >> 16
        lo = s & 0xFF; hi = (s >> 8) & 0xFF
        base = i * 4
        stereo16_4[base]   = lo; stereo16_4[base+1] = hi
        stereo16_4[base+2] = lo; stereo16_4[base+3] = hi
    spk4.write(stereo16_4)
    cnt4 += 1
    if cnt4 % 6 == 0:
        oled4.fill(0)
        oled4.rect(0, 0, 128, 64, 1)
        oled4.text("STEP4 OLED + ALL", 4, 4, 1)
        oled4.text("listen!", 36, 28, 1)
        oled4.show()
        cnt4 = 0
mic4.deinit(); spk4.deinit()
Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print("  ✓ STEP 4 完成（这就是 lesson5 的完整配置）")

print()
print("╔══════════════════════════════════════════════╗")
print("║  全部 4 步完成！                                ║")
print("║  告诉我哪一步开始出杂音 → 锁定问题根源          ║")
print("╚══════════════════════════════════════════════╝")
