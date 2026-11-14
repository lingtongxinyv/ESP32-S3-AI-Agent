"""ASR / LLM / TTS 适配层。

默认全部走 mock，先让 M2/M3 的链路跑通；换成云厂商时只改 config.yaml
里的 provider 和这里对应类的实现，设备端固件一行不用动。
"""
import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import subprocess
import struct
import time
import uuid
from urllib.parse import quote

import aiohttp


def _load_env():
    """从 gateway/.env 读凭证，已有的环境变量优先。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


# ---------------------------------------------------------------- ASR
class MockASR:
    """不做真实识别，固定返回一句话，用来验证「判停 -> 文本 -> 回复」的时序。"""

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        await asyncio.sleep(0.2)
        return "你好，介绍一下你自己"


class AliyunASR:
    """阿里云智能语音交互：CreateToken + 一句话识别 REST。

    需要三个环境变量（写在 gateway/.env）：ALIYUN_APPKEY / ALIYUN_AK_ID / ALIYUN_AK_SECRET
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.appkey = os.environ.get("ALIYUN_APPKEY", "")
        self.ak_id = os.environ.get("ALIYUN_AK_ID", "")
        self.ak_secret = os.environ.get("ALIYUN_AK_SECRET", "")
        self.region = cfg.get("region", "cn-shanghai")
        self.token_url = f"https://nls-meta.{self.region}.aliyuncs.com/"
        self.asr_url = f"https://nls-gateway-{self.region}.aliyuncs.com/stream/v1/asr"
        self._token = None
        self._token_expire = 0

    @staticmethod
    def _enc(s):
        # 阿里云 POP 签名要求的 RFC3986 编码（空格 -> %20，* -> %2A，~ 不转义）
        return quote(str(s), safe="~")

    def _signed_url(self, params):
        """RPC 风格签名：HMAC-SHA1(secret + '&', 'GET&%2F&' + 规范化查询串)"""
        items = sorted(params.items())
        canonical = "&".join(f"{self._enc(k)}={self._enc(v)}" for k, v in items)
        string_to_sign = f"GET&%2F&{self._enc(canonical)}"
        digest = hmac.new((self.ak_secret + "&").encode(),
                          string_to_sign.encode(), hashlib.sha1).digest()
        signature = base64.b64encode(digest).decode()
        return f"{self.token_url}?{canonical}&Signature={self._enc(signature)}"

    async def _get_token(self):
        if self._token and time.time() < self._token_expire - 60:
            return self._token
        if not (self.appkey and self.ak_id and self.ak_secret):
            raise RuntimeError("缺少 ALIYUN_APPKEY / ALIYUN_AK_ID / ALIYUN_AK_SECRET，请填写 gateway/.env")

        params = {
            "AccessKeyId": self.ak_id,
            "Action": "CreateToken",
            "Format": "JSON",
            "RegionId": self.region,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "Version": "2019-02-28",
        }
        async with aiohttp.ClientSession() as s:
            async with s.get(self._signed_url(params)) as r:
                data = await r.json(content_type=None)
        token = data.get("Token") or {}
        if "Id" not in token:
            raise RuntimeError(f"取 token 失败: {data}")
        self._token = token["Id"]
        self._token_expire = int(token.get("ExpireTime", 0))
        return self._token

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        token = await self._get_token()
        params = {
            "appkey": self.appkey,
            "format": "pcm",
            "sample_rate": str(rate),
            "enable_punctuation_prediction": "true",
            "enable_inverse_text_normalization": "true",
        }
        headers = {"X-NLS-Token": token, "Content-Type": "application/octet-stream"}
        async with aiohttp.ClientSession() as s:
            async with s.post(self.asr_url, params=params, data=pcm, headers=headers) as r:
                data = await r.json(content_type=None)
        if data.get("status") != 20000000:
            raise RuntimeError(f"ASR 失败: {data}")
        return data.get("result", "")


# ---------------------------------------------------------------- ASR（云端备选）
class SiliconFlowASR:
    """硅基流动 SenseVoice：OpenAI 兼容 /v1/audio/transcriptions，只需一个 API Key。"""

    URL = "https://api.siliconflow.cn/v1/audio/transcriptions"

    def __init__(self, cfg):
        self.api_key = os.environ.get("SILICONFLOW_API_KEY", "")
        self.model = cfg.get("model", "FunAudioLLM/SenseVoiceSmall")

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        if not self.api_key:
            raise RuntimeError("缺少 SILICONFLOW_API_KEY，请填写 gateway/.env")
        form = aiohttp.FormData()
        form.add_field("file", _wav_bytes(pcm, rate),
                       filename="a.wav", content_type="audio/wav")
        form.add_field("model", self.model)
        form.add_field("response_format", "json")
        async with aiohttp.ClientSession() as s:
            async with s.post(self.URL, data=form, headers={
                    "Authorization": f"Bearer {self.api_key}"}) as r:
                data = await r.json(content_type=None)
        if "text" not in data:
            raise RuntimeError(f"ASR 失败: {data}")
        return data["text"]


class LocalASR:
    """本地 faster-whisper（small/int8），免网络、延迟稳定约 1 倍实时。

    依赖 gateway/.venv；用 `.venv/bin/python l6_server.py` 启动才可用。
    """

    def __init__(self, cfg):
        self.size = cfg.get("model", "small")
        self.root = cfg.get("download_root", "/tmp/hf")
        self._model = None

    def _get(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.size, device="cpu", compute_type="int8",
                                       download_root=self.root, cpu_threads=8)
        return self._model

    def _run(self, pcm: bytes) -> str:
        import numpy as np
        a = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
        segs, _ = self._get().transcribe(
            a, language="zh", beam_size=1, condition_on_previous_text=False,
            initial_prompt="以下是普通话对话。")
        return "".join(s.text for s in segs).strip()

    async def transcribe(self, pcm: bytes, rate: int = 16000) -> str:
        return await asyncio.to_thread(self._run, pcm)


class LLM:
    """任意 OpenAI 兼容接口（DeepSeek / 豆包 / Qwen / GPT），流式出字。"""

    def __init__(self, cfg):
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = os.environ.get("DEEPSEEK_API_KEY") or cfg["api_key"]
        self.model = cfg["model"]
        self.max_tokens = cfg.get("max_tokens", 300)
        self.system = cfg.get("system", "")
        self.history = []

    def reset(self):
        self.history = []

    async def stream(self, user_text: str):
        msgs = [{"role": "system", "content": self.system}]
        msgs += self.history[-6:]
        msgs.append({"role": "user", "content": user_text})

        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": True,
            "max_tokens": self.max_tokens,
            "temperature": 0.7,
        }
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        answer = ""
        async with aiohttp.ClientSession() as s:
            async with s.post(
                self.base_url + "/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30, sock_read=10),
            ) as r:
                r.raise_for_status()
                async for raw in r.content:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    import json

                    try:
                        delta = json.loads(data)["choices"][0]["delta"]
                    except Exception:
                        continue
                    piece = delta.get("content") or ""
                    if piece:
                        answer += piece
                        yield piece
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": answer})


class XiaolingLLM:
    """直接和小綾对话：走 dsh-suite 的 A2A 对话总线（线程 + peer token）。

    上下文由小綾那边的线程维护，这里不存历史。
    """

    def __init__(self, cfg):
        self.base = cfg.get("base_url", "http://127.0.0.1:8010").rstrip("/")
        self.token = os.environ.get("XIAOLING_TOKEN", "")
        self.thread = os.environ.get("XIAOLING_THREAD", "")
        self.to = cfg.get("to", "小綾")

    def reset(self):
        pass

    async def stream(self, user_text: str):
        if not (self.token and self.thread):
            raise RuntimeError("缺少 XIAOLING_TOKEN / XIAOLING_THREAD，请填写 gateway/.env")
        url = f"{self.base}/api/chat/threads/{self.thread}/messages/stream"
        headers = {"Authorization": "Bearer " + self.token}
        payload = {"text": user_text, "to": self.to, "wait": True, "timeout": 90}
        timeout = aiohttp.ClientTimeout(total=120, sock_read=120)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json=payload, headers=headers) as r:
                r.raise_for_status()
                event = ""
                while True:
                    raw = await r.content.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", "ignore").strip()
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            data = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        if event == "delta":
                            piece = data.get("text", "")
                            if piece:
                                yield piece
                        elif event == "error":
                            raise RuntimeError(data.get("message", "小綾没接上模型"))
                        elif event == "done":
                            break


class SiliconFlowTTS:
    """硅基流动 CosyVoice2：/v1/audio/speech → 16k 单声道 s16 PCM（直接可播）。"""

    URL = "https://api.siliconflow.cn/v1/audio/speech"

    def __init__(self, cfg):
        self.api_key = os.environ.get("SILICONFLOW_API_KEY", "")
        self.model = cfg.get("model", "FunAudioLLM/CosyVoice2-0.5B")
        self.voice = cfg.get("voice", "anna")
        if ":" not in self.voice:
            # 系统预置音色必须带模型前缀，如 FunAudioLLM/CosyVoice2-0.5B:anna
            self.voice = f"{self.model}:{self.voice}"
        self.rate = int(cfg.get("sample_rate", 16000))

    async def synth(self, text: str) -> bytes:
        if not self.api_key:
            raise RuntimeError("缺少 SILICONFLOW_API_KEY，请填写 gateway/.env")
        # 注意：该模型忽略 response_format，恒返回 MP3，必须本地解码
        payload = {"model": self.model, "input": text, "voice": self.voice,
                   "response_format": "mp3"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(self.URL, json=payload, headers=headers) as r:
                if r.status != 200:
                    raise RuntimeError(f"TTS 失败 {r.status}: {await r.text()}")
                mp3 = await r.read()
        return await self._decode(mp3)

    async def _decode(self, mp3: bytes) -> bytes:
        """MP3 → 单声道 s16 PCM（8 kHz），设备端直接可播。"""
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(self.rate),
            "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate(mp3)
        if proc.returncode != 0 or not out:
            raise RuntimeError("ffmpeg 解码失败")
        return out


# ---------------------------------------------------------------- TTS
class LocalTTS:
    """本地 piper（zh_CN-huayan-medium），0.2~0.3s 出音，ffmpeg 重采样到设备采样率。"""

    def __init__(self, cfg):
        self.model_path = cfg.get("model_path", "voices/zh_CN-huayan-medium.onnx")
        self.rate = cfg.get("sample_rate", 8000)
        self._voice = None

    def _get(self):
        if self._voice is None:
            from piper import PiperVoice
            self._voice = PiperVoice.load(self.model_path)
        return self._voice

    def _run(self, text: str) -> bytes:
        voice = self._get()
        pcm = b"".join(c.audio_int16_bytes for c in voice.synthesize(text))
        src = voice.config.sample_rate
        if src == self.rate:
            return pcm
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le",
             "-ar", str(src), "-ac", "1", "-i", "pipe:0",
             "-f", "s16le", "-ar", str(self.rate), "-ac", "1", "pipe:1"],
            input=pcm, capture_output=True)
        return p.stdout

    async def synth(self, text: str) -> bytes:
        return await asyncio.to_thread(self._run, text)


class MockTTS:
    """正弦提示音，验证下行编码/播放链路。"""

    def __init__(self, rate: int):
        self.rate = rate

    async def synth(self, text: str) -> bytes:
        n = int(self.rate * 0.35)
        buf = bytearray()
        for i in range(n):
            v = int(8000 * math.sin(2 * math.pi * 660 * i / self.rate))
            buf += struct.pack("<h", v)
        return bytes(buf)


class AliyunTTS:
    """阿里云语音合成。

    TODO 接账号后补：
      1. token 同上；
      2. 建议用 REST 接口 aHR0cHM6Ly9... 返回 mp3，需要 ffmpeg 转 24k/16bit/mono PCM；
      3. 想省一步就用流式 TTS 接口直接要 pcm 格式，边收边下发。
    """

    def __init__(self, cfg, rate: int):
        self.cfg = cfg
        self.rate = rate

    async def synth(self, text: str) -> bytes:
        raise NotImplementedError("AliyunTTS 未实现：填好 AK/SK 后启用")


def build_asr(cfg):
    p = cfg.get("provider", "mock")
    if p == "aliyun":
        return AliyunASR(cfg)
    if p == "siliconflow":
        return SiliconFlowASR(cfg)
    return MockASR()


def _wav_bytes(pcm: bytes, rate: int, ch: int = 1) -> bytes:
    align = ch * 2
    hdr = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * align, align, 16)
    hdr += b"data" + struct.pack("<I", len(pcm))
    return hdr + pcm


def build_tts(cfg, rate):
    p = cfg.get("provider", "mock")
    if p == "aliyun":
        return AliyunTTS(cfg, rate)
    if p == "siliconflow":
        return SiliconFlowTTS(cfg)
    return MockTTS(rate)


def build_llm(cfg):
    p = cfg.get("provider", "openai")
    if p == "xiaoling":
        return XiaolingLLM(cfg)
    return LLM(cfg)


if __name__ == "__main__":
    # 用法: python3 providers.py uploads/mic_xxx.wav [aliyun|siliconflow]
    import sys
    import wave

    w = wave.open(sys.argv[1], "rb")
    pcm = w.readframes(w.getnframes())
    provider = sys.argv[2] if len(sys.argv) > 2 else "aliyun"
    cls = SiliconFlowASR if provider == "siliconflow" else AliyunASR
    print(asyncio.run(cls({}).transcribe(pcm, w.getframerate())))
