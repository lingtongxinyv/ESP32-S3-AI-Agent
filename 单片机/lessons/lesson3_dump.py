# L3 取证：录 3 秒原始数据，供电脑端分析字节结构
# 运行：python -m mpremote connect COM11 run lessons/lesson3_dump.py
# 之后把两个文件拉回电脑：
#   python -m mpremote connect COM11 fs cp :raw32.bin raw32.bin
#   python -m mpremote connect COM11 fs cp :raw16.bin raw16.bin
from machine import I2S, Pin
import time

RATE = 16000
btn = Pin(4, Pin.IN, Pin.PULL_UP)

def rec(bits, path, seconds=3):
    a = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7), mode=I2S.RX,
            bits=bits, format=I2S.MONO, rate=RATE, ibuf=8192)
    n = RATE * seconds
    buf = bytearray(n * (bits // 8))
    a.readinto(buf)
    a.deinit()
    with open(path, "wb") as f:
        f.write(buf)
    # 统计：每个字节位置里 0 的占比（判断数据是靠左还是靠右对齐）
    step = bits // 8
    zeros = [0] * step
    total = len(buf) // step
    for i in range(len(buf)):
        if buf[i] == 0:
            zeros[i % step] += 1
    print("saved", path, len(buf), "bytes, step", step, "zero-ratio per byte:",
          ["%.0f%%" % (z * 100 / total) for z in zeros])
    return buf

print("按一下按键开始，然后对着麦克风持续说话 6 秒")
while btn.value() == 1:
    time.sleep_ms(10)
while btn.value() == 0:
    time.sleep_ms(10)

print("recording 32-bit ...")
rec(32, "raw32.bin", 3)
print("recording 16-bit ...")
rec(16, "raw16.bin", 3)
print("done")
