from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import struct
import time
from collections.abc import AsyncIterator

import httpx

from .config import Settings
from .panel import EMOTIONS


_EMOTION_MARKER_RE = re.compile(r"\[\[(?:" + "|".join(EMOTIONS) + r")\]\]", re.IGNORECASE)
_PARTIAL_EMOTION_MARKER_RE = re.compile(r"\[{1,2}[A-Za-z]*$")


def wav_bytes(pcm: bytes, rate: int = 16000) -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


def persona_tick(system: str) -> str:
    """小綾式每轮人格锚：从人设提炼一句短锚，注入每轮 prompt，防长上下文稀释人格。

    成本约 60-100 字符/轮；只在与记忆段一起注入（上下文最长的时候才需要锚）。
    """
    head = " ".join((system or "").split())[:120]
    if not head:
        return ""
    return f"【人格锚·每轮】记住你的人设，始终以此人设的口吻与自称回复：{head}"


def _compose_messages(
    system: str, history: list[dict[str, str]], user_text: str, extra_system: str | None
) -> list[dict[str, str]]:
    """永续锚放 system 层；人格锚 + 记忆段拼成最后一条 system，紧贴本轮 user 消息（近因效应）。"""
    messages: list[dict[str, str]] = (
        [{"role": "system", "content": system}] if system else []
    )
    messages.extend(history)
    if extra_system is not None:
        anchor = persona_tick(system)
        extra = f"{anchor}\n\n{extra_system}" if anchor else extra_system
        messages.append({"role": "system", "content": extra})
    messages.append({"role": "user", "content": user_text})
    return messages


class MockASR:
    async def transcribe(self, pcm: bytes, rate: int) -> str:
        return "你好，介绍一下你自己"


class MockLLM:
    async def stream(
        self,
        user_text: str,
        history: list[dict[str, str]],
        extra_system: str | None = None,
    ) -> AsyncIterator[str]:
        yield "你好，"
        yield "我是 ESP32-S3 AI Agent。"


class MockTTS:
    def __init__(self, rate: int = 16000):
        self.rate = rate

    async def synth(self, text: str) -> bytes:
        samples = int(self.rate * 0.2)
        return b"".join(
            struct.pack(
                "<h", int(8000 * math.sin(2 * math.pi * 660 * index / self.rate))
            )
            for index in range(samples)
        )


class SiliconFlowASR:
    url = "https://api.siliconflow.cn/v1/audio/transcriptions"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        if not self.settings.siliconflow_api_key:
            raise RuntimeError("未配置 SILICONFLOW_API_KEY")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self.url,
                headers={"Authorization": f"Bearer {self.settings.siliconflow_api_key}"},
                files={"file": ("audio.wav", wav_bytes(pcm, rate), "audio/wav")},
                data={"model": self.settings.asr_model, "response_format": "json"},
            )
        if response.status_code != 200:
            raise RuntimeError(f"ASR HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data.get("text"), str):
            raise RuntimeError("ASR 响应缺少 text")
        return data["text"].strip()


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def stream(
        self,
        user_text: str,
        history: list[dict[str, str]],
        extra_system: str | None = None,
    ) -> AsyncIterator[str]:
        if not self.settings.openai_api_key:
            raise RuntimeError("未配置 OPENAI_API_KEY")
        messages = _compose_messages(
            self.settings.llm_system, history, user_text, extra_system
        )
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 300,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            async with client.stream(
                "POST",
                f"{self.settings.openai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"LLM HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"]
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
                    piece = delta.get("content") or ""
                    if piece:
                        yield piece


class XiaolingLLM:
    """小綾对话总线；线程和凭据必须由服务端按账号路由显式绑定。"""

    def __init__(self, settings: Settings, thread_id: str = "", token: str = ""):
        self.settings = settings
        self.thread_id = thread_id
        self.token = token
        self._emotion: str | None = None

    def pop_emotion(self) -> str | None:
        emotion = self._emotion
        self._emotion = None
        return emotion

    async def stream(
        self,
        user_text: str,
        history: list[dict[str, str]],
        extra_system: str | None = None,
    ) -> AsyncIterator[str]:
        settings = self.settings
        self._emotion = None
        if not (self.thread_id and self.token):
            raise RuntimeError("小綾对话缺少按账号绑定的线程或凭据")
        url = (
            f"{settings.xiaoling_base_url}/api/chat/threads/"
            f"{self.thread_id}/messages/stream"
        )
        payload = {
            "text": user_text,
            "to": settings.xiaoling_to,
            "wait": True,
            "timeout": 90,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
            async with client.stream(
                "POST",
                url,
                headers={"Authorization": f"Bearer {self.token}"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"小綾 HTTP {response.status_code}")
                event = ""
                yielded = False
                pending = ""
                async for raw in response.aiter_lines():
                    line = raw.strip()
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:") and event in {"delta", "done", "emotion"}:
                        try:
                            data = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        piece = ""
                        if event == "delta":
                            piece = data.get("text", "")
                        elif event == "done" and not yielded:
                            # /status、/task 等命令不经过模型，只在 done 返回完整回复。
                            piece = (data.get("post") or {}).get("text", "")
                        elif event == "emotion" and self._emotion is None:
                            emotion = data.get("emotion")
                            if isinstance(emotion, str) and emotion.lower() in EMOTIONS:
                                self._emotion = emotion.lower()
                        if piece:
                            yielded = True
                            # 情绪标记是协议元数据，可跨 delta 到达，不能交给 TTS 朗读。
                            combined = pending + piece
                            if self._emotion is None:
                                marker = _EMOTION_MARKER_RE.search(combined)
                                if marker:
                                    self._emotion = marker.group(0)[2:-2].lower()
                            piece = _EMOTION_MARKER_RE.sub("", combined)
                            pending = ""
                            partial = _PARTIAL_EMOTION_MARKER_RE.search(piece)
                            if partial:
                                pending = partial.group(0)
                                piece = piece[: partial.start()]
                            if piece:
                                yield piece
                if pending:
                    yield pending


class AdminCommandGuardLLM:
    """管理员 /task 必须二次确认；普通聊天仍走原小綾线程。"""

    def __init__(self, inner: XiaolingLLM, user_id: int, pending: dict[int, tuple[float, str]]):
        self.inner = inner
        self.user_id = user_id
        self.pending = pending

    def pop_emotion(self) -> str | None:
        return self.inner.pop_emotion()

    async def stream(
        self,
        user_text: str,
        history: list[dict[str, str]],
        extra_system: str | None = None,
    ) -> AsyncIterator[str]:
        text = user_text.strip()
        if text.startswith("/task") and text[5:].strip():
            self.pending[self.user_id] = (time.monotonic() + 60, text)
            yield "检测到管理员任务指令。请在 60 秒里说“确认执行”，我才会交给小綾值守会话。"
            return
        if text.rstrip("。！!") == "确认执行":
            pending = self.pending.pop(self.user_id, None)
            if pending is None or pending[0] < time.monotonic():
                yield "没有待确认的管理员任务，或确认时间已过期。"
                return
            async for piece in self.inner.stream(pending[1], history, extra_system=extra_system):
                yield piece
            return
        # 插入其他对话即取消待确认任务，避免之后的“确认执行”误触发旧指令。
        self.pending.pop(self.user_id, None)
        async for piece in self.inner.stream(user_text, history, extra_system=extra_system):
            yield piece


class SiliconFlowTTS:
    url = "https://api.siliconflow.cn/v1/audio/speech"

    def __init__(self, settings: Settings):
        self.settings = settings
        voice = settings.tts_voice
        self.voice = voice if ":" in voice else f"{settings.tts_model}:{voice}"
        self.speed = float(settings.tts_speed)

    async def synth(self, text: str, speed: float | None = None) -> bytes:
        if not self.settings.siliconflow_api_key:
            raise RuntimeError("未配置 SILICONFLOW_API_KEY")
        payload = {
            "model": self.settings.tts_model,
            "input": text,
            "voice": self.voice,
            "response_format": "mp3",
        }
        spd = speed if speed is not None else self.speed
        if spd and abs(spd - 1.0) > 1e-6:
            payload["speed"] = round(spd, 2)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self.url,
                headers={"Authorization": f"Bearer {self.settings.siliconflow_api_key}"},
                json=payload,
            )
        if response.status_code != 200:
            raise RuntimeError(f"TTS HTTP {response.status_code}")
        return await decode_to_pcm(response.content)


async def decode_to_pcm(encoded_audio: bytes, rate: int = 16000) -> bytes:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-f",
        "s16le",
        "-ac",
        "1",
        "-ar",
        str(rate),
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await process.communicate(encoded_audio)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode != 0 or not output:
        detail = error.decode("utf-8", "replace")[:300]
        raise RuntimeError(f"ffmpeg 解码失败: {detail}")
    return output


class DialogueProvider:
    def __init__(self, settings: Settings):
        self.mode = settings.dialogue_provider
        if self.mode == "cloud":
            self.asr = SiliconFlowASR(settings)
            self.llm = OpenAICompatibleLLM(settings)
            self.tts = SiliconFlowTTS(settings)
        elif self.mode == "xiaoling":
            self.asr = SiliconFlowASR(settings)
            self.llm = XiaolingLLM(settings)
            self.tts = SiliconFlowTTS(settings)
        elif self.mode in {"mock", "echo"}:
            self.asr = MockASR()
            self.llm = MockLLM()
            self.tts = MockTTS()
        else:
            raise ValueError("DIALOGUE_PROVIDER 必须为 echo、mock、cloud 或 xiaoling")
        self.memory_llm = self.llm

    def routed(self, llm, memory_llm) -> "DialogueProvider":
        """保留 ASR/TTS，只替换当前账号的聊天与蒸馏通道。"""
        clone = copy.copy(self)
        clone.llm = llm
        clone.memory_llm = memory_llm
        return clone


def split_sentences(buffer: str) -> tuple[list[str], str]:
    sentences: list[str] = []
    current = ""
    for char in buffer:
        current += char
        if char in "。！？!?；;\n":
            if current.strip():
                sentences.append(current.strip())
            current = ""
    return sentences, current


MAX_TTS_CHARS = 400


def split_tts_chunks(text: str) -> list[str]:
    """单次 TTS 文本硬上限切块：超长文本会产生超额合成计费。"""
    return [
        text[index : index + MAX_TTS_CHARS]
        for index in range(0, len(text), MAX_TTS_CHARS)
    ]


async def tts_pcm(provider, text: str) -> bytes:
    """按句切分 + 400 字符硬切块再合成，拼接成一段 PCM（REST 与 WS 共用）。"""
    pcm = bytearray()
    # 最后一道边界：任何 LLM 包装层都不能把控制标记交给 TTS。
    buffer = _EMOTION_MARKER_RE.sub("", text).strip()
    while buffer:
        sentences, buffer = split_sentences(buffer)
        if not sentences:
            sentences, buffer = [buffer], ""
        for sentence in sentences:
            for chunk in split_tts_chunks(sentence):
                if chunk.strip():
                    pcm.extend(await provider.tts.synth(chunk))
    return bytes(pcm)


class BundleASR:
    """按客户端配置包里的服务商跑识别，统一 OpenAI 兼容协议。"""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    async def transcribe(self, pcm: bytes, rate: int) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("audio.wav", wav_bytes(pcm, rate), "audio/wav")},
                data={"model": self.model, "response_format": "json"},
            )
        if response.status_code != 200:
            raise RuntimeError(f"ASR HTTP {response.status_code}")
        text = response.json().get("text")
        if not isinstance(text, str):
            raise RuntimeError("ASR 响应缺少 text")
        return text.strip()


class BundleLLM:
    def __init__(self, base_url: str, api_key: str, model: str, system: str) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.system = system

    async def stream(
        self,
        user_text: str,
        history: list[dict[str, str]],
        extra_system: str | None = None,
    ) -> AsyncIterator[str]:
        messages = _compose_messages(self.system, history, user_text, extra_system)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 300,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"LLM HTTP {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"]
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
                    piece = delta.get("content") or ""
                    if piece:
                        yield piece


class BundleTTS:
    def __init__(
        self, base_url: str, api_key: str, model: str, voice: str, speed: float = 1.0
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        # 与 SiliconFlowTTS 口径一致：没带 model 前缀时自动补全，避免用户只填音色名。
        self.voice = voice if (voice and ":" in voice) else (f"{model}:{voice}" if model and voice else voice)
        self.speed = float(speed)

    async def synth(self, text: str, speed: float | None = None) -> bytes:
        payload = {
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "response_format": "mp3",
        }
        spd = speed if speed is not None else self.speed
        if spd and abs(spd - 1.0) > 1e-6:
            payload["speed"] = round(spd, 2)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        if response.status_code != 200:
            raise RuntimeError(f"TTS HTTP {response.status_code}")
        return await decode_to_pcm(response.content)


class ProfileDialogue:
    """由客户端配置包构造的一轮对话能力，密钥只在内存中使用。"""

    mode = "profile"

    def __init__(self, asr: BundleASR, llm: BundleLLM, tts: BundleTTS) -> None:
        self.asr = asr
        self.llm = llm
        self.memory_llm = llm
        self.tts = tts


def dialogue_from_profile(bundle: dict, settings: Settings) -> ProfileDialogue | None:
    """配置包不完整时返回 None，调用方回退到服务器默认 provider。"""
    try:
        llm = bundle["llm"]
        asr = bundle["asr"]
        tts = bundle["tts"]
        llm_base = str(llm["base_url"]).strip().rstrip("/")
        llm_key = str(llm["api_key"]).strip()
        asr_base = str(asr["base_url"]).strip().rstrip("/")
        asr_key = str(asr["api_key"]).strip()
        tts_base = str(tts["base_url"]).strip().rstrip("/")
        tts_key = str(tts["api_key"]).strip()
        tts_voice = str(tts.get("voice") or "").strip()
        tts_speed = float(tts.get("speed") or 1.0)
    except (KeyError, TypeError, AttributeError):
        return None
    if not (llm_base and llm_key and asr_base and asr_key and tts_base and tts_key):
        return None
    persona = str(bundle.get("persona") or "").strip() or settings.llm_system
    return ProfileDialogue(
        asr=BundleASR(asr_base, asr_key, str(asr.get("model") or "")),
        llm=BundleLLM(llm_base, llm_key, str(llm.get("model") or ""), persona),
        tts=BundleTTS(
            tts_base,
            tts_key,
            str(tts.get("model") or ""),
            tts_voice,
            tts_speed,
        ),
    )
