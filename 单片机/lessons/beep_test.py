from machine import I2S, Pin
import math, time

print('=== MAX98357A STEREO self-test ===')
RATE = 16000
spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17), mode=I2S.TX,
          bits=16, format=I2S.STEREO, rate=RATE, ibuf=8192)

n = RATE
buf = bytearray(n * 4)
for i in range(n):
    v = int(12000 * math.sin(2 * math.pi * 440 * i / RATE))
    lo = v & 0xFF; hi = (v >> 8) & 0xFF
    base = i * 4
    buf[base]   = lo;  buf[base+1] = hi
    buf[base+2] = lo;  buf[base+3] = hi

print('playing 440Hz beep for 1s...')
spk.write(buf)
time.sleep_ms(100)
spk.deinit()
Pin(15, Pin.IN); Pin(16, Pin.IN); Pin(17, Pin.IN)
print('done')