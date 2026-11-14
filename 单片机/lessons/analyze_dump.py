import struct

def wav_write(path, samples, rate=16000):
    with open(path, "wb") as f:
        data = struct.pack("<" + "h" * len(samples), *samples)
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
        f.write(b"data" + struct.pack("<I", len(data)) + data)

# --- raw32: 每 4 字节 [0, D1, D2, D3]，取 D1/D2 作为 16 位样本 ---
raw = open("raw32.bin", "rb").read()
samples = []
for i in range(0, len(raw), 4):
    v = (raw[i + 1] << 8) | raw[i + 2]          # 高 16 位
    if v >= 0x8000:
        v -= 0x10000
    samples.append(v)
wav_write("mic32_fixed.wav", samples)
print("mic32_fixed.wav  samples=%d peak=%d" % (len(samples), max(max(samples), -min(samples))))

# --- raw16: 16 位模式直接录的，看有没有声音 ---
raw = open("raw16.bin", "rb").read()
samples = list(struct.unpack("<%dh" % (len(raw) // 2), raw))
wav_write("mic16_direct.wav", samples)
print("mic16_direct.wav samples=%d peak=%d" % (len(samples), max(max(samples), -min(samples))))
