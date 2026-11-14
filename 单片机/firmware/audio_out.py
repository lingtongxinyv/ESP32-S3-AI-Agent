# I2S 功放播放：MAX98357A
# 关键：MAX98357A 要求 BCLK = LRCLK × 32/48/64
# MONO 模式 BCLK 只有 16k×16=256kHz，不匹配 → 持续噪声
# 必须用 STEREO 模式，然后把 MONO 数据复制到左右声道
from machine import I2S, Pin
import config


class Speaker:
    """MAX98357A 功放封装。

    板子 SD/MODE 引脚可能没引出（产品页接口表只有 8 脚），
    所以 SD 引脚通过 config.SPK_SD 配置，None = 不接。
    """

    def __init__(self):
        # SD 引脚：板子可能没引，None 表示不接
        self.sd_pin = None
        if config.SPK_SD is not None:
            self.sd_pin = Pin(config.SPK_SD, Pin.OUT, value=0)

        self.i2s = None
        self._open()

        # 预分配 stereo 缓冲，避免 write() 里每次新建
        # 40ms @ 24k 16bit STEREO = 24000 * 0.04 * 2 * 2 = 3840 字节
        self._stereo_buf = bytearray(int(config.SPK_RATE * 0.04 * 4))

    def _open(self):
        self.i2s = I2S(
            1,
            sck=Pin(config.SPK_BCLK),
            ws=Pin(config.SPK_LRC),
            sd=Pin(config.SPK_DIN),
            mode=I2S.TX,
            bits=16,
            format=I2S.STEREO,           # 必须 STEREO！BCLK 才对
            rate=config.SPK_RATE,
            ibuf=config.SPK_IBUF,
        )

    def _mono2stereo(self, mono_bytes):
        """把 MONO PCM 复制到左右声道，原地写入 self._stereo_buf。"""
        n_samples = len(mono_bytes) // 2
        # 确保缓冲够大
        need = n_samples * 4
        if len(self._stereo_buf) < need:
            self._stereo_buf = bytearray(need)
        s = self._stereo_buf
        for i in range(n_samples):
            mi = i * 2
            si = i * 4
            s[si]   = mono_bytes[mi];   s[si+1] = mono_bytes[mi+1]
            s[si+2] = mono_bytes[mi];   s[si+3] = mono_bytes[mi+1]
        return memoryview(s)[:need]

    def write(self, pcm_bytes):
        """写入 MONO PCM 数据（16bit），内部自动转 STEREO 给 MAX98357A。"""
        if self.sd_pin is not None and not self.sd_pin.value():
            self.sd_pin.value(1)
        stereo = self._mono2stereo(pcm_bytes)
        self.i2s.write(stereo)

    def mute(self):
        """如果板子引了 SD/MODE 引脚，拉低关功放。"""
        if self.sd_pin is not None:
            self.sd_pin.value(0)

    def flush(self):
        """打断时清空：发一段静音 + 重建 I2S。"""
        # 先发 50ms 静音，消掉管道里残留的声音
        silence = bytes(int(config.SPK_RATE * 0.05 * 4))  # STEREO 全 0
        try:
            self.i2s.write(silence)
        except Exception:
            pass
        self.mute()
        try:
            self.i2s.deinit()
        except Exception:
            pass
        self._open()

    def close(self):
        self.mute()
        try:
            self.i2s.deinit()
        except Exception:
            pass
