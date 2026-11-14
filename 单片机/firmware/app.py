# 主状态机 + 4 个协作 task：采集 / 收包 / 播放 / 按键UI
import uasyncio as asyncio
import json
import config
import ui_oled
import net_ws
from audio_in import Mic, energy
from audio_out import Speaker
from button import Button

IDLE = "IDLE"
LISTEN = "LISTEN"
THINK = "THINK"
SPEAK = "SPEAK"
ERROR = "ERROR"


class Robot:
    def __init__(self):
        self.state = IDLE
        self.level = 0
        self.text = ""
        self.step = 0
        self.mic = Mic()
        self.spk = Speaker()
        self.btn = Button(config.BTN_PIN)
        self.link = net_ws.Link()
        self.q = asyncio.Queue()      # 下行 PCM 抖动缓冲
        self.speech_ms = 0
        self.silence_ms = 0
        self._dirty = True

    # ---------- 状态切换 ----------
    def set_state(self, s):
        if self.state != s:
            self.state = s
            self.step = 0
            self._dirty = True

    async def start_listen(self):
        if not self.link.alive():
            return
        self.speech_ms = 0
        self.silence_ms = 0
        self.text = ""
        self.link.send_text('{"t":"start","sr":%d}' % config.MIC_RATE)
        self.set_state(LISTEN)

    async def end_speech(self):
        self.link.send_text('{"t":"end"}')
        self.set_state(THINK)

    async def abort(self):
        if self.link.alive():
            self.link.send_text('{"t":"abort"}')
        self.spk.flush()
        while not self.q.empty():
            self.q.get_nowait()
        self.set_state(IDLE)

    # ---------- task 1: 麦克风 ----------
    async def mic_task(self):
        while True:
            pcm = self.mic.read()
            e = energy(pcm)
            # 只在 LISTEN 时用麦克风电平驱动 UI，避免播放时眼睛乱跳
            if self.state == LISTEN:
                self.level = e
                try:
                    self.link.send_bin(pcm.tobytes())
                except Exception:
                    self.set_state(ERROR)     # 由 rx_task 负责重连
                    continue
                if e > config.VAD_ON:
                    self.speech_ms += config.MIC_FRAME_MS
                    self.silence_ms = 0
                elif self.speech_ms >= config.VAD_MIN_SPEECH_MS:
                    self.silence_ms += config.MIC_FRAME_MS
                    if self.silence_ms >= config.VAD_SILENCE_MS:
                        await self.end_speech()
            await asyncio.sleep_ms(0)

    # ---------- task 2: 收包 ----------
    async def rx_task(self):
        while True:
            try:
                data = self.link.recv()
                if not data:
                    await asyncio.sleep_ms(10)
                    continue
                if data[0] == 0x7B:                      # '{' -> JSON 控制帧
                    self.on_json(json.loads(data))
                elif data[0] == 0x01:                    # 0x01 -> PCM
                    if self.q.qsize() < config.PLAY_QUEUE_MAX:
                        self.q.put_nowait(data[1:])
                    if self.state == THINK:
                        self.set_state(SPEAK)
            except Exception:
                await self.reconnect()

    def on_json(self, m):
        t = m.get("t")
        if t == "state":
            v = m.get("v")
            if v == "thinking":
                self.set_state(THINK)
            elif v == "idle":
                self.set_state(IDLE)
        elif t == "text":
            self.text = (self.text + m.get("d", ""))[-120:]
            self._dirty = True
        elif t == "done":
            self.set_state(IDLE)
        elif t == "error":
            self.set_state(ERROR)

    # ---------- task 3: 播放 ----------
    async def play_task(self):
        while True:
            chunk = await self.q.get()
            if self.state != SPEAK:
                continue
            try:
                self.spk.write(chunk)      # 阻塞约 40ms/帧
            except Exception:
                self.set_state(ERROR)
                continue
            self.level = 1200

    # ---------- task 4: 按键 ----------
    async def btn_task(self):
        while True:
            k = self.btn.poll()
            if k == "short":
                if self.state == IDLE:
                    await self.start_listen()
                elif self.state == LISTEN:
                    await self.end_speech()
                else:
                    await self.abort()
            elif k == "long":
                self.text = ""
                await self.reconnect()
            await asyncio.sleep_ms(20)

    # ---------- task 5: UI ----------
    async def ui_task(self):
        while True:
            self.step += 1
            ui_oled.draw(self.state, self.text, self.level, self.step)
            if self.state in (IDLE, THINK):
                self.level = 0
            await asyncio.sleep_ms(120)

    # ---------- 重连 ----------
    async def reconnect(self):
        self.spk.mute()
        self.link.close()
        self.set_state(ERROR)
        for i in range(5):
            try:
                net_ws.connect_wifi()
                self.link.open()
                await self.start_listen()
                return
            except Exception:
                await asyncio.sleep(1 + i)

    async def run(self):
        net_ws.connect_wifi()
        self.link.open()
        ui_oled.draw(IDLE)
        await asyncio.gather(
            self.mic_task(),
            self.rx_task(),
            self.play_task(),
            self.btn_task(),
            self.ui_task(),
        )
