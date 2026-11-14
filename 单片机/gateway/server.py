"""设备音频网关：ESP32-S3 <-> WebSocket <-> ASR/LLM/TTS

运行：
    pip install -r requirements.txt
    python server.py

协议（二进制帧首字节区分类型）：
    设备上行  : JSON 文本帧 {"t":"hello|start|end|abort"} + 裸 PCM(16k/16bit/mono)
    服务下行  : JSON 文本帧 {"t":"state|text|done|error"} + b"\\x01" + PCM(24k/16bit/mono)
"""
import array
import asyncio
import json
import pathlib
import time
import wave

import websockets
import yaml

import providers

CFG = yaml.safe_load(open(pathlib.Path(__file__).with_name("config.yaml"), encoding="utf-8"))
A = CFG["audio"]
FRAME_OUT = A["out_rate"] * A["frame_ms"] // 1000 * 2   # 每帧字节数
LOGDIR = pathlib.Path(__file__).with_name("logs")
LOGDIR.mkdir(exist_ok=True)


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    """线性插值重采样，够用就行（16k -> 24k）。"""
    x = array.array("h")
    x.frombytes(pcm)
    n = len(x)
    if not n:
        return b""
    m = int(n * dst / src)
    y = array.array("h", bytes(2 * m))
    step = src / dst
    for i in range(m):
        p = i * step
        i0 = int(p)
        f = p - i0
        a = x[i0]
        b = x[i0 + 1] if i0 + 1 < n else a
        y[i] = int(a + (b - a) * f)
    return y.tobytes()


async def send_pcm(ws, pcm: bytes):
    """按 40ms 节奏下发，模拟真实流式，让设备的抖动缓冲正常工作。"""
    for off in range(0, len(pcm), FRAME_OUT):
        await ws.send(b"\x01" + pcm[off: off + FRAME_OUT])
        await asyncio.sleep(A["frame_ms"] / 1000)


async def send_json(ws, **kw):
    await ws.send(json.dumps(kw, ensure_ascii=False))


def split_sentences(text_buf: str):
    """按中英文标点切句，返回 (完整句子列表, 剩余未成句部分)。"""
    out, cur = [], ""
    for ch in text_buf:
        cur += ch
        if ch in "。！？!?；;\n":
            if cur.strip():
                out.append(cur.strip())
            cur = ""
    return out, cur


class Session:
    def __init__(self, ws):
        self.ws = ws
        self.mode = CFG["server"].get("mode", "echo")
        self.uplink = bytearray()
        self.llm = providers.build_llm(CFG["llm"])
        self.asr = providers.build_asr(CFG["asr"])
        self.tts = providers.build_tts(CFG["tts"], A["out_rate"])
        self.t0 = 0.0
        self.n_frames = 0

    async def run(self):
        peer = self.ws.remote_address
        print(f"[+] {peer} connected, mode={self.mode}")
        async for msg in self.ws:
            if isinstance(msg, (bytes, bytearray)):
                self.uplink += msg
                self.n_frames += 1
                continue
            try:
                m = json.loads(msg)
            except Exception:
                continue
            t = m.get("t")
            if t == "start":
                self.uplink = bytearray()
                self.n_frames = 0
                self.t0 = time.time()
                self.llm.reset()
                await send_json(self.ws, t="state", v="idle")
                print("  <- start")
            elif t == "end":
                dur = len(self.uplink) / 2 / A["in_rate"]
                print(f"  <- end, {self.n_frames} frames, {dur:.2f}s audio")
                if A.get("dump_wav"):
                    self._dump()
                try:
                    if self.mode == "echo":
                        await self.echo()
                    else:
                        await self.voice()
                except Exception as e:
                    print("  !! pipeline error:", repr(e))
                    await send_json(self.ws, t="error", d=repr(e))
            elif t == "abort":
                print("  <- abort")
                self.uplink = bytearray()

    def _dump(self):
        p = LOGDIR / time.strftime("up_%Y%m%d_%H%M%S.wav")
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(A["in_rate"])
            w.writeframes(bytes(self.uplink))
        print("  saved", p.name)

    async def echo(self):
        pcm = resample(bytes(self.uplink), A["in_rate"], A["out_rate"])
        await send_json(self.ws, t="state", v="thinking")
        await send_json(self.ws, t="text", d="[echo]")
        await send_pcm(self.ws, pcm)
        await send_json(self.ws, t="done")
        print(f"  -> echo {len(pcm)/2/A['out_rate']:.2f}s, cost={time.time()-self.t0:.2f}s")

    async def voice(self):
        text = await self.asr.transcribe(bytes(self.uplink), A["in_rate"])
        print("  ASR:", text)
        await send_json(self.ws, t="text", d="You: " + text)
        await send_json(self.ws, t="state", v="thinking")

        buf = ""
        spoke = False
        async for piece in self.llm.stream(text):
            await send_json(self.ws, t="text", d=piece)
            buf += piece
            sents, buf = split_sentences(buf)
            for s in sents:
                if not spoke:
                    await send_json(self.ws, t="state", v="speaking")
                    spoke = True
                await send_pcm(self.ws, await self.tts.synth(s))
        if buf.strip():
            if not spoke:
                await send_json(self.ws, t="state", v="speaking")
            await send_pcm(self.ws, await self.tts.synth(buf))

        await send_json(self.ws, t="done")
        print(f"  -> voice done, cost={time.time()-self.t0:.2f}s")


async def handler(ws):
    await Session(ws).run()


async def main():
    host = CFG["server"]["host"]
    port = CFG["server"]["port"]
    print(f"gateway listening on {host}:{port}  mode={CFG['server']['mode']}")
    async with websockets.serve(handler, host, port, max_size=1 << 20):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
