# L3+L4 纯离线录放：先录 3 秒，再播 3 秒
# 完全不碰 OLED，避免 SoftI2C 干扰硬 I2S DMA
# 运行：按一次按键开始录，录完自动播
from machine import I2S, Pin
import time, gc

RATE = 16000
BITS = 16
btn = Pin(4, Pin.IN, Pin.PULL_UP)

# --- 录音：只开 RX ---------------------------------------
print("按一下按键，开始录音 3 秒（对着麦克风说话）")
while btn.value() == 1: time.sleep_ms(10)
while btn.value() == 0: time.sleep_ms(10)

n = RATE * 3
buf = bytearray(n * 2)
print("recording...")
gc.collect()
mic = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7), mode=I2S.RX,
          bits=BITS, format=I2S.MONO, rate=RATE, ibuf=8192)
mic.readinto(buf)
mic.deinit()
print("done recording, peak =", max(buf))

# 存一份到 flash，方便拉回电脑听
with open("offline.wav", "wb") as f:
    f.write(b"RIFF")
    f.write((36 + len(buf)).to_bytes(4, "little"))
    f.write(b"WAVEfmt ")
    f.write((16).to_bytes(4, "little"))
    f.write((1).to_bytes(2, "little"))     # PCM
    f.write((1).to_bytes(2, "little"))     # mono
    f.write(RATE.to_bytes(4, "little"))
    f.write((RATE * 2).to_bytes(4, "little"))
    f.write((2).to_bytes(2, "little"))
    f.write((16).to_bytes(2, "little"))
    f.write(b"data")
    f.write(len(buf).to_bytes(4, "little"))
    f.write(buf)
print("saved offline.wav")

# --- 播放：只开 TX ---------------------------------------
print("按一下按键，开始播放 3 秒")
while btn.value() == 1: time.sleep_ms(10)
while btn.value() == 0: time.sleep_ms(10)

print("playing...")
gc.collect()

# !!! MAX98357A 要求 BCLK 必须是 LRCLK 的 32/48/64 倍 !!!
# MONO 模式 BCLK = 16k*16 = 256kHz → 不满足任何倍数 → 持续噪声
# STEREO 模式 BCLK = 16k*2*16 = 512kHz = 32×LRCLK → 完美
spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17), mode=I2S.TX,
          bits=BITS, format=I2S.STEREO, rate=RATE, ibuf=8192)

# MONO buf → STEREO buf：每个采样复制到左右两个声道
# MAX98357A 会自动把左右声道平均后输出（(L/2+R/2) 模式）
stereo_buf = bytearray(len(buf) * 2)
for i in range(0, len(buf), 2):
    stereo_buf[i*2]   = buf[i]       # 左声道
    stereo_buf[i*2+1] = buf[i+1]
    stereo_buf[i*2+2] = buf[i]       # 右声道 = 同样的数据
    stereo_buf[i*2+3] = buf[i+1]

spk.write(stereo_buf)
spk.deinit()
# 把 BCLK/LRC/DIN 引脚设回输入，避免悬空干扰
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print("done")
