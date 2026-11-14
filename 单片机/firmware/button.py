# 轻触按键：短按 / 长按，软件去抖
from machine import Pin
import time

SHORT_MIN = 20
SHORT_MAX = 800
LONG = 800


class Button:
    def __init__(self, pin):
        self.pin = Pin(pin, Pin.IN, Pin.PULL_UP)
        self._last = 1
        self._t0 = 0
        self._long_sent = False

    def poll(self):
        """返回 None / 'short' / 'long'。建议 20ms 调一次。"""
        v = self.pin.value()
        now = time.ticks_ms()
        if self._last == 1 and v == 0:           # 按下
            self._t0 = now
            self._long_sent = False
        elif self._last == 0 and v == 1:         # 抬起
            dt = time.ticks_diff(now, self._t0)
            self._last = v
            if SHORT_MIN < dt < SHORT_MAX:
                return "short"
            return None
        elif v == 0 and not self._long_sent:     # 一直按着
            if time.ticks_diff(now, self._t0) >= LONG:
                self._long_sent = True
                self._last = v
                return "long"
        self._last = v
        return None
