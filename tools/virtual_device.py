#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets


HEADER = struct.Struct("<BBHIII")
PROTOCOL_VERSION = 1
UPLINK = 1
DOWNLINK = 2


def encode_audio_frame(frame_type: int, session: int, sequence: int, payload: bytes) -> bytes:
    return HEADER.pack(PROTOCOL_VERSION, frame_type, 0, session, sequence, len(payload)) + payload


def decode_audio_frame(data: bytes, expected_type: int = DOWNLINK) -> tuple[int, int, bytes]:
    if len(data) < HEADER.size:
        raise ValueError("音频帧头不足 16 字节")
    version, frame_type, _flags, session, sequence, size = HEADER.unpack_from(data)
    if version != PROTOCOL_VERSION or frame_type != expected_type:
        raise ValueError("音频帧版本或方向错误")
    if size != len(data) - HEADER.size:
        raise ValueError("音频帧长度错误")
    return session, sequence, data[HEADER.size:]


@dataclass
class DialogueResult:
    session: int
    audio: bytes
    asr_text: str = ""
    reply_text: str = ""
    events: list[dict] = field(default_factory=list)


class VirtualDevice:
    """独立 websocket 设备，也可被集成测试直接复用。默认走公网 query 认证。"""

    def __init__(
        self,
        url: str,
        token: str,
        device_id: str = "virtual-device",
        fw_version: str = "virtual-1",
        protocol: str = "1",
        hardware: str = "esp32-s3",
        use_query_token: bool = True,
    ):
        self.base_url = url
        self.token = token
        self.device_id = device_id
        self.fw_version = fw_version
        self.protocol = protocol
        self.hardware = hardware
        self.use_query_token = use_query_token
        self.websocket = None

    def connection_url(self) -> str:
        if not self.use_query_token:
            return self.base_url
        parts = urlsplit(self.base_url)
        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "token"]
        query.append(("token", self.token))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    async def __aenter__(self) -> "VirtualDevice":
        headers = {} if self.use_query_token else {"Authorization": f"Bearer {self.token}"}
        self.websocket = await websockets.connect(
            self.connection_url(), additional_headers=headers, max_size=4 * 1024 * 1024
        )
        await self.send_json(
            "hello",
            0,
            device_id=self.device_id,
            fw_version=self.fw_version,
            protocol=self.protocol,
            hardware=self.hardware,
        )
        while True:
            message = await self.recv_json()
            if message["t"] == "hello":
                return self
            if message["t"] == "error":
                raise RuntimeError(message.get("message", "hello 失败"))

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.websocket is not None:
            await self.websocket.close()

    async def send_json(self, event_type: str, session: int, **payload) -> None:
        if self.websocket is None:
            raise RuntimeError("设备尚未连接")
        await self.websocket.send(
            json.dumps({"v": 1, "t": event_type, "session": session, **payload}, ensure_ascii=False)
        )

    async def receive(self):
        if self.websocket is None:
            raise RuntimeError("设备尚未连接")
        return await asyncio.wait_for(self.websocket.recv(), timeout=30)

    async def recv_json(self) -> dict:
        message = await self.receive()
        if isinstance(message, bytes):
            raise RuntimeError("等待控制消息时收到音频")
        data = json.loads(message)
        if data.get("v") != 1:
            raise RuntimeError("响应协议版本错误")
        return data

    async def dialogue(self, session: int, pcm: bytes, chunk_size: int = 640) -> DialogueResult:
        if len(pcm) % 2:
            raise ValueError("PCM s16le 长度必须为偶数")
        await self.send_json("start", session, sample_rate=16000)
        for sequence, offset in enumerate(range(0, len(pcm), chunk_size)):
            await self.websocket.send(
                encode_audio_frame(UPLINK, session, sequence, pcm[offset : offset + chunk_size])
            )
        await self.send_json("end", session)

        audio = bytearray()
        asr_parts: list[str] = []
        reply_parts: list[str] = []
        events: list[dict] = []
        expected_sequence = 0
        while True:
            message = await self.receive()
            if isinstance(message, bytes):
                frame_session, sequence, payload = decode_audio_frame(message)
                if frame_session != session or sequence != expected_sequence:
                    raise RuntimeError("下行音频 session 或 sequence 不连续")
                audio.extend(payload)
                expected_sequence += 1
                continue
            event = json.loads(message)
            if event.get("v") != 1:
                raise RuntimeError("响应协议版本错误")
            events.append(event)
            event_type = event.get("t")
            if event_type == "error":
                raise RuntimeError(f"服务端错误 {event.get('code')}: {event.get('message')}")
            if event.get("session") != session:
                continue
            if event_type == "asr_result":
                asr_parts.append(event.get("text", ""))
            elif event_type == "llm_delta":
                reply_parts.append(event.get("text", ""))
            elif event_type == "done":
                return DialogueResult(
                    session=session,
                    audio=bytes(audio),
                    asr_text="".join(asr_parts),
                    reply_text="".join(reply_parts),
                    events=events,
                )

    async def abort(self, session: int) -> None:
        await self.send_json("abort", session)

    async def ping(self, session: int = 0) -> dict:
        await self.send_json("ping", session)
        while True:
            message = await self.recv_json()
            if message["t"] == "pong":
                return message


def read_pcm(path: Path, raw: bool) -> bytes:
    if raw or path.suffix.lower() in {".pcm", ".raw"}:
        data = path.read_bytes()
        if len(data) % 2:
            raise ValueError("PCM 文件长度必须为偶数")
        return data
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 16000:
            raise ValueError("WAV 必须为 16 kHz、单声道、s16le")
        return source.readframes(source.getnframes())


async def run(args: argparse.Namespace) -> dict:
    pcm = read_pcm(args.input, args.pcm)
    async with VirtualDevice(
        args.url,
        args.token,
        device_id=args.device_id,
        fw_version=args.fw_version,
        protocol=args.protocol,
        use_query_token=not args.header_auth,
    ) as device:
        result = await device.dialogue(args.session, pcm, args.chunk_size)
    echo_match = result.audio == pcm
    if args.expect_echo and not echo_match:
        raise RuntimeError(f"echo 校验失败：上行 {len(pcm)} 字节，下行 {len(result.audio)} 字节")
    return {
        "ok": True,
        "session": result.session,
        "uplink_bytes": len(pcm),
        "downlink_bytes": len(result.audio),
        "echo_match": echo_match,
        "asr_text": result.asr_text,
        "reply_text": result.reply_text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ESP32-S3 WebSocket 虚拟设备")
    parser.add_argument("--url", default="ws://127.0.0.1:8781/ws/v1/device")
    parser.add_argument("--token", default=os.getenv("DEVICE_TOKEN", ""))
    parser.add_argument("--device-id", default="virtual-device")
    parser.add_argument("--fw-version", default="virtual-1")
    parser.add_argument("--protocol", default="1")
    parser.add_argument("--session", type=int, default=1)
    parser.add_argument("--input", type=Path, required=True, help="16k/mono/s16le WAV 或 PCM")
    parser.add_argument("--pcm", action="store_true", help="按裸 PCM 读取输入")
    parser.add_argument("--chunk-size", type=int, default=640)
    parser.add_argument("--expect-echo", action="store_true", help="校验下行 PCM 与输入逐字节一致")
    parser.add_argument("--header-auth", action="store_true", help="本机改用 Authorization；公网默认 query")
    args = parser.parse_args()
    if not args.token:
        parser.error("请通过 DEVICE_TOKEN 或 --token 提供设备令牌")
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        # websockets 异常可能包含完整 URI；显式清除令牌后再输出。
        message = str(exc).replace(args.token, "<redacted>")
        print(f"虚拟设备失败：{message}")
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
