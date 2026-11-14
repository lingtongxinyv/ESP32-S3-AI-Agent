# ============================================================
# L6: 按键触发录 2 秒 → 分块流式上传裸 PCM 到自建网关
# 使用前请填写下方 WiFi 与服务器配置。
# 上传: http://your-server.example.com/esp32/upload
#       裸 PCM(s16le 单声道) + HTTP/1.1 keep-alive，不再 base64/JSON
#       （公网 443/80 只有 nginx 入口，8765 不对公网开放）
# 服务端: gateway/l6_server.py 监听 127.0.0.1:8780，由 nginx /esp32/ 反代
# ============================================================
from machine import I2S, Pin, SoftI2C
import ssd1306, time, struct, gc, network, usocket, ujson, binascii

# ---- 配置 ---------------------------------------------------------------
WIFI_SSID = '你的WiFi名'      # 中文 SSID 需要 encode
WIFI_PASS = '你的WiFi密码'
SERVER_HOST = 'your-server.example.com'
SERVER_PORT = 80
SERVER_PATH = '/esp32/voice'  # 返回小綾的语音答复（PCM 8k）
CHUNK = 4096                  # 每块发送字节数

RATE = 8000          # 上行 8k：数据减半，服务端转 16k 喂 ASR
REC_SECONDS = 2
N = RATE * REC_SECONDS

# ---- OLED + 按键 ---------------------------------------------------------
i2c = SoftI2C(scl=Pin(12), sda=Pin(11), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
btn = Pin(4, Pin.IN, Pin.PULL_UP)
Pin(17, Pin.OUT, value=0)          # 功放常开（SD 硬接 VIN），DIN 必须被钉住

# ---- WiFi 连接 ----------------------------------------------------------
def connect_wifi():
    print("连接 WiFi...")
    oled.fill(0)
    oled.rect(0, 0, 128, 64, 1)
    oled.text("L6 WIFI UPLOAD", 2, 4, 1)
    oled.hline(0, 16, 128, 1)
    oled.text("connecting...", 20, 30, 1)
    oled.show()

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        aps = wlan.scan()
        names = [a[0].decode('utf-8', 'replace') for a in aps]
        print(f"  扫到 {len(aps)} 个热点, 目标SSID在其中: {WIFI_SSID in names}")
    except Exception as e:
        print("  scan 失败:", e)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID.encode('utf-8'), WIFI_PASS)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 20000:
                print("WiFi 连接超时！")
                return None
            time.sleep_ms(500)
    ip = wlan.ifconfig()[0]
    print(f"WiFi 已连接: IP={ip}")
    return wlan

# ---- 录音 ---------------------------------------------------------------
def record_pcm():
    """开麦录 REC_SECONDS 秒 → 关麦 → 返回 s16le 单声道 PCM"""
    print(f"  REC {REC_SECONDS}s...")
    oled.fill(0)
    oled.rect(0, 0, 128, 64, 1)
    oled.text("RECORDING...", 18, 28, 1)
    oled.text("2 sec, speak!", 20, 44, 1)
    oled.show()

    mic = I2S(0, sck=Pin(5), ws=Pin(6), sd=Pin(7),
              mode=I2S.RX, bits=32, format=I2S.MONO, rate=RATE, ibuf=8192)
    raw32 = bytearray(N * 4)
    mic.readinto(raw32)
    mic.deinit()
    Pin(5, Pin.IN); Pin(6, Pin.IN); Pin(7, Pin.IN)
    mic = None
    print("  录完，关麦 ✅")

    # 32bit → 16bit MONO（WAV 要的是 MONO 原始数据）
    mono16 = bytearray(N * 2)
    vals = struct.unpack('<%di' % N, raw32)
    peak = 0
    for i in range(N):
        s = vals[i] >> 16
        if s > 32767: s -= 65536
        if abs(s) > peak: peak = abs(s)
        lo = s & 0xFF; hi = (s >> 8) & 0xFF
        mono16[i*2] = lo; mono16[i*2+1] = hi
    print(f"  peak={peak}/32767")
    del raw32; del vals; gc.collect()

    print(f"  PCM {len(mono16)} bytes")
    out = bytes(mono16)
    del mono16; gc.collect()
    return out

# ---- 上传：HTTP/1.1 keep-alive + 分块发送裸 PCM --------------------------
class Uploader:
    def __init__(self, host, port, path):
        self.host = host
        self.port = port
        self.path = path
        self.sock = None

    def _close(self):
        if self.sock:
            try: self.sock.close()
            except: pass
        self.sock = None

    def _open(self):
        ai = usocket.getaddrinfo(self.host, self.port)[0]
        s = usocket.socket()
        s.settimeout(40)
        s.connect(ai[-1])
        self.sock = s
        print("  TCP connected")

    def _read_resp(self):
        line = self.sock.readline()
        code = int(line.split(b' ')[1]) if line else 0
        hdrs = {}
        clen = 0
        while True:
            h = self.sock.readline()
            if not h or h in (b'\r\n', b'\n'):
                break
            k, _, v = h.partition(b':')
            k = k.strip().lower()
            hdrs[k] = v.strip()
            if k == b'content-length':
                clen = int(v)
        body = b''
        while len(body) < clen:
            d = self.sock.read(clen - len(body))
            if not d: break
            body += d
        return code, hdrs, body

    def post(self, pcm, rate):
        """连不上/断开时自动重连一次；成功后连接保留给下一次按键"""
        for attempt in (0, 1):
            try:
                if self.sock is None:
                    self._open()
                hdr = ('POST %s HTTP/1.1\r\nHost: %s\r\n'
                       'Content-Type: application/octet-stream\r\n'
                       'X-Rate: %d\r\nContent-Length: %d\r\n'
                       'Connection: keep-alive\r\n\r\n'
                       % (self.path, self.host, rate, len(pcm))).encode()
                self.sock.write(hdr)
                mv = memoryview(pcm)
                for i in range(0, len(pcm), CHUNK):
                    self.sock.write(mv[i:i + CHUNK])
                return self._read_resp()
            except Exception as e:
                self._close()
                if attempt:
                    raise

# ---- 播放小綾的回复（8k mono s16 → 3 倍上采样到 24k 立体声）-------------
PLAY_RATE = 24000             # 实测 8k/16k 有嗡嗡声，只有 24k 时钟稳定
VOLUME = 2.0                  # 倍数增益，TTS 输出峰值只有 -15dB，需要放大；>1 会削顶

def play_pcm(pcm, rate):
    scale = PLAY_RATE // rate
    n = len(pcm) // 2
    print(f"  PLAY {len(pcm)/1024:.1f} KB @ {rate}Hz x{scale} vol={VOLUME}")
    oled.fill(0)
    oled.rect(0, 0, 128, 64, 1)
    oled.text("SPEAKING...", 22, 28, 1)
    oled.show()

    # MAX98357A 要求 BCLK 是 LRCLK 的 32/48/64 倍，所以必须 STEREO
    # 交接文档结论：连续多次 write() 会出杂音，必须整段一次写完
    st = bytearray(n * scale * 4)
    o = 0
    for k in range(n):
        a = pcm[k*2] | (pcm[k*2+1] << 8)
        if a >= 32768: a -= 65536
        j = k + 1
        if j < n:
            b = pcm[j*2] | (pcm[j*2+1] << 8)
            if b >= 32768: b -= 65536
        else:
            b = a
        d1 = (b - a) // 3
        for m in (a, a + d1, a + 2 * d1):      # 线性插值上采样，比零阶保持干净
            v = int(m * VOLUME)
            if v > 32767: v = 32767
            elif v < -32768: v = -32768
            lo = v & 0xFF; hi = (v >> 8) & 0xFF
            st[o] = lo; st[o+1] = hi           # 左
            st[o+2] = lo; st[o+3] = hi         # 右
            o += 4
    gc.collect()

    spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(17), mode=I2S.TX,
              bits=16, format=I2S.STEREO, rate=PLAY_RATE, ibuf=32768)
    spk.write(st)                              # 单次写入，不再分批
    spk.deinit()
    Pin(15, Pin.IN); Pin(16, Pin.IN)
    Pin(17, Pin.OUT, value=0)                  # DIN 拉低：功放常开，避免浮空拾噪
    print("  播放完毕")


# ---- 主循环 ---------------------------------------------------------------
def draw_idle():
    oled.fill(0)
    oled.rect(0, 0, 128, 64, 1)
    oled.text("L6 OK - UPLOAD", 2, 4, 1)
    oled.hline(0, 16, 128, 1)
    oled.text("press = REC 2s", 6, 28, 1)
    oled.text("then upload", 14, 44, 1)
    oled.show()

print("=" * 50)
print("L6: 录 → POST 到 your-server.example.com")
print("=" * 50)

# 1. 连 WiFi
wlan = connect_wifi()
while wlan is None:
    print("WiFi 连不上，10s 后重试（Ctrl-C 可停）...")
    time.sleep(10)
    wlan = connect_wifi()
draw_idle()
up = Uploader(SERVER_HOST, SERVER_PORT, SERVER_PATH)

# 2. 主循环
while True:
    # 检查 WiFi 还在
    if not wlan.isconnected():
        print("WiFi 断了，重连...")
        wlan.connect(WIFI_SSID.encode('utf-8'), WIFI_PASS)
        t0 = time.ticks_ms()
        while not wlan.isconnected() and time.ticks_diff(time.ticks_ms(), t0) < 20000:
            time.sleep_ms(500)

    # 等按键
    if btn.value() == 0:
        time.sleep_ms(30)
        if btn.value() == 0:
            time.sleep_ms(500)   # 松手后再录，避免按键声
            if btn.value() != 0:
                print("-> TRIGGER")
                pcm = record_pcm()
                oled.fill(0)
                oled.rect(0, 0, 128, 64, 1)
                oled.text("UPLOADING...", 22, 28, 1)
                oled.show()
                t0 = time.ticks_ms()
                body = b''
                hdrs = {}
                try:
                    code, hdrs, body = up.post(pcm, RATE)
                    ok = 200 <= code < 300
                    dt = time.ticks_diff(time.ticks_ms(), t0)
                    print(f"  HTTP {code} in {dt}ms, {len(body)} bytes")
                    if ok:
                        txt = binascii.a2b_base64(hdrs.get(b'x-text-b64', b'')).decode()
                        rep = binascii.a2b_base64(hdrs.get(b'x-reply-b64', b'')).decode()
                        print("  听到:", txt)
                        print("  小綾:", rep)
                except Exception as e:
                    ok = False
                    print("  上传失败:", e)
                del pcm; gc.collect()

                # 显示出结果，然后播语音
                oled.fill(0)
                oled.rect(0, 0, 128, 64, 1)
                oled.text("RESULT", 36, 4, 1)
                oled.hline(0, 16, 128, 1)
                if ok:
                    oled.text("OK!", 52, 28, 1)
                    oled.text(f"{len(body)//1024} KB", 40, 44, 1)
                else:
                    oled.text("FAIL!", 46, 28, 1)
                oled.show()

                if ok and body:
                    try:
                        play_pcm(body, int(hdrs.get(b'x-rate', b'8000')))
                    except Exception as e:
                        print("  播放失败:", e)
                del body; gc.collect()
                time.sleep_ms(500)
                draw_idle()

    time.sleep_ms(20)
