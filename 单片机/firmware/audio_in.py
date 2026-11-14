# I2S 麦克风采集：ICS43434 (24bit 左对齐) -> 16bit PCM 帧
import array
import struct
from machine import I2S, Pin
import config


class Mic:
    def __init__(self):
        self.samples = config.MIC_RATE * config.MIC_FRAME_MS // 1000
        self._raw = bytearray(self.samples * 4)   # 32bit/采样
        self._fmt = "<%di" % self.samples
        self.i2s = None
        self._open()

    def _open(self):
        self.i2s = I2S(
            0,
            sck=Pin(config.MIC_SCK),
            ws=Pin(config.MIC_WS),
            sd=Pin(config.MIC_SD),
            mode=I2S.RX,
            bits=config.MIC_BITS,
            format=I2S.MONO,
            rate=config.MIC_RATE,
            ibuf=config.MIC_IBUF,
        )

    def read(self):
        """读一帧，返回 array('h')，长度 = samples。"""
        self.i2s.readinto(self._raw)
        v = struct.unpack(self._fmt, self._raw)
        # 24bit 左对齐放在高 24 位，取高 16 位
        return array.array("h", (x >> 16 for x in v))

    def close(self):
        try:
            self.i2s.deinit()
        except Exception:
            pass


def energy(pcm):
    """单帧平均绝对值，用作 VAD 判据。安静约 20~80，说话 800~4000。"""
    s = 0
    for x in pcm:
        s += x if x >= 0 else -x
    return s // len(pcm)
