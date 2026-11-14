# OLED 界面：状态表情 + ASCII 字幕 + 音量条
from machine import Pin, SoftI2C
import ssd1306
import config

IDLE = "IDLE"
LISTEN = "LISTEN"
THINK = "THINK"
SPEAK = "SPEAK"
ERROR = "ERROR"

_oled = None


def init():
    global _oled
    i2c = SoftI2C(
        scl=Pin(config.I2C_SCL),
        sda=Pin(config.I2C_SDA),
        freq=config.I2C_FREQ,
    )
    _oled = ssd1306.SSD1306_I2C(config.OLED_W, config.OLED_H, i2c)
    return _oled


def _eyes(expand=0, blink=False):
    # 两只眼睛：圆角方块
    if blink:
        _oled.fill_rect(24, 28, 20, 4, 1)
        _oled.fill_rect(84, 28, 20, 4, 1)
    else:
        w, h = 20 + expand, 22 + expand
        _oled.fill_rect(24, 26 - expand // 2, w, h, 1)
        _oled.fill_rect(84, 26 - expand // 2, w, h, 1)


def _dots(step):
    for i in range(3):
        y = 34 - (4 if (step + i) % 3 == 0 else 0)
        _oled.fill_rect(44 + i * 18, y, 8, 8, 1)


def _mouth(level, step):
    h = 4 + (level * 18) // 4000 + (step % 2) * 2
    h = min(h, 22)
    _oled.fill_rect(46, 36 - h // 2, 36, h, 1)


def draw(state, text="", level=0, step=0):
    if _oled is None:
        return
    _oled.fill(0)

    if state == IDLE:
        _eyes()
        _oled.text("...", 56, 42)
    elif state == LISTEN:
        _eyes(expand=4 if level > config.VAD_ON else 0)
        bar = min(level * 96 // 6000, 96)
        _oled.rect(16, 52, 96, 8, 1)
        _oled.fill_rect(18, 54, bar, 4, 1)
    elif state == THINK:
        _dots(step)
    elif state == SPEAK:
        _mouth(level, step)
    elif state == ERROR:
        for i in range(10):
            _oled.pixel(58 + i, 24 + i, 1)
            _oled.pixel(67 - i, 24 + i, 1)
        _oled.text("ERR", 52, 40)

    # 字幕：只用 ASCII，中文关键词另外做点阵。8px 字体一行 16 个字符
    if text:
        line = text[-16:]
        _oled.text(line, 0, 56)

    _oled.show()
