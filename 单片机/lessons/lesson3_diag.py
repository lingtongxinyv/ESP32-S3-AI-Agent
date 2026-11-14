# L3 诊断：测 I2S 各种配置下能否读到非零数据
# 结果 peak=0 全部为 0 => 接线/供电问题；某项非 0 => 配置问题
from machine import I2S, Pin
import time

def test(bits, fmt, rate=16000, n=8000):
    try:
        a = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7), mode=I2S.RX,
                bits=bits, format=fmt, rate=rate, ibuf=8192)
        buf = bytearray(int(rate * 0.5) * (bits // 8))
        a.readinto(buf)
        peak = 0
        step = bits // 8
        for i in range(0, len(buf), step):
            v = buf[i] if buf[i] < 128 else buf[i] - 256
            if abs(v) > peak:
                peak = abs(v)
        a.deinit()
        return peak
    except Exception as e:
        return "ERR " + str(e)

print("--- 按一下按键开始（之后持续说话 5 秒）---")
btn = Pin(4, Pin.IN, Pin.PULL_UP)
while btn.value() == 1:
    time.sleep_ms(10)
print("--- 开始诊断 ---")
for bits in (16, 32, 24):
    for fmt, name in ((I2S.MONO, "MONO"), (I2S.STEREO, "STEREO")):
        time.sleep_ms(300)
        print("bits=%d %-6s peak=%s" % (bits, name, test(bits, fmt)))
print("--- 结束 ---")
