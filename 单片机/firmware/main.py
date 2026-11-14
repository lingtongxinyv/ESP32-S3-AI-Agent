# 启动入口：开机动画 -> 连 WiFi -> 进主循环
import time
import uasyncio as asyncio
import config
import ui_oled
import app


def splash():
    oled = ui_oled.init()
    oled.fill(0)
    oled.text("AI Robot", 32, 20)
    oled.text("booting...", 28, 40)
    oled.show()
    time.sleep_ms(600)


def main():
    splash()
    robot = app.Robot()
    try:
        asyncio.run(robot.run())
    except KeyboardInterrupt:
        robot.mic.close()
        robot.spk.close()
        print("bye")


main()
