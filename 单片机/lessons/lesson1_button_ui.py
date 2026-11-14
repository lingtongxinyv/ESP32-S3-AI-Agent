# L1 实验：按键切换 OLED 三个界面
# 运行方式：电脑上执行  python -m mpremote connect COM11 run lessons/lesson1_button_ui.py
# 停止：在电脑上按 Ctrl+C，或按板子上的 RST 复位

from machine import Pin, SoftI2C
import ssd1306
import time

# ---- 1. 初始化外设 ------------------------------------------------------
# OLED 走 I2C：SDA=GPIO11, SCL=GPIO12（昨天 L0 验证过的接法）
i2c = SoftI2C(scl=Pin(12), sda=Pin(11), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

# 按键走 GPIO：一脚接 GPIO4，另一脚接 GND
# PULL_UP = 开内部上拉：不按读 1，按下读 0（低电平有效）
btn = Pin(4, Pin.IN, Pin.PULL_UP)

# ---- 2. 定义三个界面 -----------------------------------------------------
# 每个界面就是一个函数，负责把一屏内容画完
PAGES = ["IDLE", "REC", "SET"]   # 界面名字

def draw(page):
    oled.fill(0)                   # 清屏（全部像素熄灭）
    oled.rect(0, 0, 128, 64, 1)    # 外框，方便看出刷新
    oled.text("L1 Button UI", 8, 4, 1)
    oled.hline(0, 16, 128, 1)      # 标题下的分隔线
    if page == 0:
        oled.text("State: IDLE", 8, 28, 1)
        oled.text("press -> next", 8, 44, 1)
    elif page == 1:
        oled.text("State: REC", 8, 28, 1)
        oled.fill_rect(8, 44, 12, 12, 1)   # 画个实心方块当“录音点”
        oled.text("recording...", 26, 46, 1)
    else:
        oled.text("State: SET", 8, 28, 1)
        oled.text("vol: - 5 +", 8, 44, 1)
    oled.show()                    # 把缓冲区真正刷到屏幕上

# ---- 3. 主循环：检测按键 + 切换界面 --------------------------------------
page = 0
draw(page)
print("L1 running, press the button...")

last = 1          # 上一次读到的电平
last_ms = time.ticks_ms()   # 上一次状态变化的时间（消抖用）

while True:
    v = btn.value()
    # 只在“电平变化”时处理，并且距上次变化超过 30ms（消抖）
    if v != last and time.ticks_diff(time.ticks_ms(), last_ms) > 30:
        last = v
        last_ms = time.ticks_ms()
        if v == 0:                       # 只响应“按下去”这一下（1 变 0）
            page = (page + 1) % len(PAGES)
            draw(page)
            print("page ->", PAGES[page])
    time.sleep_ms(5)      # 歇 5ms 再读，CPU 不用空转
