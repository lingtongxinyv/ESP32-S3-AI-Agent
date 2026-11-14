# WiFi 连接 + WebSocket 客户端封装
# 依赖：把 micropython-lib 的 websocket.py 拷到设备根目录
import time
import network
import config

try:
    import usocket as _sock  # noqa: F401
except ImportError:
    import socket as _sock  # noqa: F401


def connect_wifi(timeout_s=20):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return wlan
    wlan.connect(config.WIFI_SSID, config.WIFI_PASS)
    t = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), t) > timeout_s * 1000:
            raise OSError("wifi timeout")
        time.sleep_ms(200)
    return wlan


class Link:
    def __init__(self):
        self.ws = None

    def open(self):
        import websocket

        self.close()
        self.ws = websocket.websocket(config.GATEWAY, timeout=10)
        self.send_text('{"t":"hello","fw":"%s"}' % config.FW_VER)

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    def send_text(self, s):
        self.ws.write(s)

    def send_bin(self, data):
        # micropython-lib 的 websocket.write 第二个参数是 opcode，2 = binary
        self.ws.write(data, 2)

    def recv(self):
        """返回一帧 payload(bytes)，连接断开时抛 OSError。"""
        return self.ws.read()

    def alive(self):
        return self.ws is not None
