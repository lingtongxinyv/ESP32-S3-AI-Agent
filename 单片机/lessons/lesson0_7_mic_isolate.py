# ============================================================
# 麦克风独立诊断：录 → 关麦 → 只有功放放
# 目的：判断麦克风本身有没有问题
#
# 步骤：
#   1. 只开麦克风，录 5 秒（请你说话，或安静环境）
#   2. 关掉麦克风！deinit，引脚回输入
#   3. 只开功放，播放刚才录的数据
#   4. 再安静录 3 秒，放出来对比底噪
#
# 判断：
#   录的声音清晰 → 麦克风没问题，杂音在直通阶段产生
#   安静环境录出来全是杂音 → 麦克风/供电有问题
# ============================================================
from machine import I2S, Pin, SoftI2C
import time, struct, gc

RATE = 16000

print("=" * 50)
print("麦克风独立诊断")
print("=" * 50)

# ====== 阶段 1: 录 5 秒（请说话）======
print()
print("阶段 1: 只开麦克风，录 5 秒")
print("  请对着麦克风说话!")

mic = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
          mode=I2S.RX, bits=32, format=I2S.MONO, rate=RATE, ibuf=8192)

# 5 秒 = 80000 samples, 32bit → 320000 字节
# 但先做 MONO 16bit 转换存，省内存
gc.collect()
N = RATE * 5    # 80000 samples
raw32 = bytearray(N * 4)   # 32bit 原始 → 320KB
print("  开始录音 5 秒...")
t0 = time.ticks_ms()
mic.readinto(raw32)
dt = time.ticks_diff(time.ticks_ms(), t0)
print(f"  录音完成，耗时 {dt}ms")

# 立刻关掉麦克风！！！
mic.deinit()
Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
mic = None
print("  麦克风已关闭 ✅")

# 32bit → 16bit STEREO（功放要的格式）
print("  转换数据...")
stereo_buf = bytearray(N * 4)   # STEREO 16bit → 320KB
vals = struct.unpack('<%di' % N, raw32)
peak = 0
for i in range(N):
    s = vals[i] >> 16
    if s < 0:
        s16 = s + 65536
    else:
        s16 = s
    if s16 > 32767: s16 -= 65536
    if abs(s16) > peak: peak = abs(s16)
    lo = s16 & 0xFF; hi = (s16 >> 8) & 0xFF
    base = i * 4
    stereo_buf[base]   = lo; stereo_buf[base+1] = hi
    stereo_buf[base+2] = lo; stereo_buf[base+3] = hi
print(f"  转换完成，峰值 = {peak}/32767")
if peak < 100:
    print("  ⚠️ 峰值太低！麦克风可能没录到声音！")

# ====== 阶段 2: 播放刚才录的（只有功放）======
print()
print("阶段 2: 只开功放播放（麦克风已关）")
print("  听：应该是你 5 秒前说的话，清晰吗？还是全是杂音？")

spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
          mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)

t0 = time.ticks_ms()
spk.write(stereo_buf)
dt = time.ticks_diff(time.ticks_ms(), t0)
print(f"  播放完成，耗时 {dt}ms")
spk.deinit()
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
spk = None

print()
print("你听到了什么？")
print("  选项 A: 清晰听到自己说的话（可能有小底噪）→ 麦克风没问题")
print("  选项 B: 全是杂音，完全听不到自己说什么 → 麦克风/接线有问题")
print("  选项 C: 有声音但严重失真/断断续 → I2S 配置或数据格式问题")

# ====== 阶段 3: 安静环境录 3 秒对比底噪 ======
print()
print("阶段 3: 安静环境录 3 秒（请勿说话！）→ 立刻播放")
print("  保持安静...")
gc.collect()

mic = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
          mode=I2S.RX, bits=32, format=I2S.MONO, rate=RATE, ibuf=8192)
N2 = RATE * 3   # 48000 samples
raw32_2 = bytearray(N2 * 4)
t0 = time.ticks_ms()
mic.readinto(raw32_2)
print(f"  安静录音完成，耗时 {time.ticks_diff(time.ticks_ms(), t0)}ms")
mic.deinit()
Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
mic = None

# 转 STEREO + 算底噪
stereo_buf2 = bytearray(N2 * 4)
vals2 = struct.unpack('<%di' % N2, raw32_2)
noise_peak = 0
noise_sum = 0
for i in range(N2):
    s = vals2[i] >> 16
    if s > 32767: s -= 65536
    if abs(s) > noise_peak: noise_peak = abs(s)
    noise_sum += abs(s)
    lo = s & 0xFF; hi = (s >> 8) & 0xFF
    base = i * 4
    stereo_buf2[base]   = lo; stereo_buf2[base+1] = hi
    stereo_buf2[base+2] = lo; stereo_buf2[base+3] = hi
noise_avg = noise_sum // N2

print(f"  安静底噪: peak={noise_peak}, avg={noise_avg}")
if noise_peak < 200:
    print("  ✅ 底噪很低，麦克风本身没问题")
else:
    print(f"  ⚠️ 底噪较大（peak={noise_peak}），可能是供电噪声或麦克风质量问题")

# 播放安静录音
print("  播放安静录音...")
spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
          mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)
spk.write(stereo_buf2)
spk.deinit()
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
spk = None

print()
print("=" * 50)
print("全部完成！总结：")
print(f"  你说话那段峰值 = {peak}/32767")
print(f"  安静底噪峰值   = {noise_peak}/32767")
print(f"  安静底噪平均   = {noise_avg}")
print()
print("请告诉我阶段 2 和阶段 3 你分别听到了什么")
print("=" * 50)
