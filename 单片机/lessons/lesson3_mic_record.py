# L3 实验：只接麦克风（ICS43434），录 5 秒存成 WAV
# 运行：python -m mpremote connect COM11 run lessons/lesson3_mic_record.py
# 跑完在设备里生成 test.wav，再用
#   python -m mpremote connect COM11 fs cp :test.wav test.wav
# 拉回电脑播放

from machine import I2S, Pin
import time

# ---- I2S 参数：麦克风和后面要用的功放必须完全一致 -------------------------
RATE = 16000      # 采样率 16 kHz（语音识别最常用，够清楚又省带宽）
BITS = 16         # 每个采样 16 位

# ---- 初始化 I2S 接收（麦克风 -> 板子）------------------------------------
# 注意 I2S.ID 0 对应硬件 I2S0，引脚可自定义
audio_in = I2S(
    0,
    sck=Pin(5),          # 位时钟 BCLK/SCK
    ws=Pin(6),           # 左右声道时钟 WS/LRCK
    sd=Pin(7),           # 数据线（麦克风输出 -> 板子输入）
    mode=I2S.RX,         # RX = 接收
    bits=BITS,
    format=I2S.MONO,     # 单声道（L/R 脚接 GND 时数据在左声道）
    rate=RATE,
    ibuf=8192,           # 接收缓冲区，太小会丢数据
)

# ---- 等按键按下再开始录（复用 L1 的按键：GPIO4 -> GND）-------------------
btn = Pin(4, Pin.IN, Pin.PULL_UP)
print("press button to start recording...")
while btn.value() == 1:
    time.sleep_ms(10)

# ---- 读 5 秒数据到内存 ---------------------------------------------------
n_samples = RATE * 5
buf = bytearray(n_samples * 2)   # 16bit = 每采样 2 字节
print("recording 5s ... speak now!")
t0 = time.ticks_ms()
audio_in.readinto(buf)           # 阻塞式读取，读满为止
dt = time.ticks_diff(time.ticks_ms(), t0)
print("done in", dt, "ms  (理论 5000 ms)")

# ---- 顺手算一下音量，判断有没有真的录到东西 ------------------------------
peak = 0
for i in range(0, len(buf), 2):
    v = buf[i] if buf[i] < 128 else buf[i] - 256      # 有符号
    if v < 0:
        v = -v
    if v > peak:
        peak = v
print("peak amplitude =", peak, "/ 32767   (≈0 说明没声音或接线错)")

# ---- 写 WAV 文件（标准 44 字节头）---------------------------------------
import struct
with open("test.wav", "wb") as f:
    f.write(b"RIFF")
    f.write(struct.pack("<I", 36 + len(buf)))
    f.write(b"WAVEfmt ")
    f.write(struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16))
    f.write(b"data")
    f.write(struct.pack("<I", len(buf)))
    f.write(buf)
print("saved test.wav", len(buf), "bytes")

audio_in.deinit()
