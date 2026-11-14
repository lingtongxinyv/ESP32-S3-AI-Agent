# ============================================================
# TEST: 双 I2S + tiny sleep 能不能消掉杂音？
# 这个脚本就是 STEP 3 加一个 time.sleep_ms(1)
# 如果杂音消失 → 问题是死循环抢了 DMA 时间片
# 如果还杂音 → 不是这个原因
# ============================================================
from machine import I2S, Pin
import time, struct

RATE = 16000

# STEP 3A: 双 I2S + 全速死循环（有杂音的那个）
print("=== 3A: 双 I2S + 全速死循环 (5秒) ===")
spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
          mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)
mic = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
          mode=I2S.RX, bits=32, format=I2S.MONO, rate=RATE, ibuf=8192)
raw32 = bytearray(320 * 4)
stereo16 = bytearray(320 * 4)
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 5000:
    mic.readinto(raw32)
    vals = struct.unpack('<%di' % 320, raw32)
    for i in range(320):
        s = vals[i] >> 16
        lo = s & 0xFF; hi = (s >> 8) & 0xFF
        base = i * 4
        stereo16[base] = lo; stereo16[base+1] = hi
        stereo16[base+2] = lo; stereo16[base+3] = hi
    spk.write(stereo16)
mic.deinit(); spk.deinit()
Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print("3A 完成")
time.sleep_ms(1000)

# STEP 3B: 双 I2S + time.sleep_ms(1) 在循环末尾
print("=== 3B: 双 I2S + sleep_ms(1) 每次循环 (5秒) ===")
spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
          mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)
mic = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
          mode=I2S.RX, bits=32, format=I2S.MONO, rate=RATE, ibuf=8192)
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 5000:
    mic.readinto(raw32)
    vals = struct.unpack('<%di' % 320, raw32)
    for i in range(320):
        s = vals[i] >> 16
        lo = s & 0xFF; hi = (s >> 8) & 0xFF
        base = i * 4
        stereo16[base] = lo; stereo16[base+1] = hi
        stereo16[base+2] = lo; stereo16[base+3] = hi
    spk.write(stereo16)
    time.sleep_ms(1)            # ← 就加这一行！
mic.deinit(); spk.deinit()
Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print("3B 完成")
time.sleep_ms(1000)

# STEP 3C: 双 I2S + 更大的 ibuf (16384) + 全速
print("=== 3C: 双 I2S + 更大 buffer ibuf=16384 全速 (5秒) ===")
spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17),
          mode=I2S.TX, bits=16, format=I2S.STEREO, rate=RATE, ibuf=16384)
mic = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
          mode=I2S.RX, bits=32, format=I2S.MONO, rate=RATE, ibuf=16384)
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 5000:
    mic.readinto(raw32)
    vals = struct.unpack('<%di' % 320, raw32)
    for i in range(320):
        s = vals[i] >> 16
        lo = s & 0xFF; hi = (s >> 8) & 0xFF
        base = i * 4
        stereo16[base] = lo; stereo16[base+1] = hi
        stereo16[base+2] = lo; stereo16[base+3] = hi
    spk.write(stereo16)
mic.deinit(); spk.deinit()
Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print("3C 完成")
time.sleep_ms(1000)

print()
print("╔══════════════════════════════════════════════╗")
print("║  3段全部听完了！                                ║")
print("║  告诉我哪段安静 / 哪段有杂音 → 锁定根因          ║")
print("╚══════════════════════════════════════════════╝")
print()
print("3A: 全速死循环 → 应该有杂音（之前 STEP 3 的情况）")
print("3B: 每次循环 sleep_ms(1) → 如果安静了就找到了！")
print("3C: 更大 buffer 全速 → 如果安静说明 buffer 不够大")
