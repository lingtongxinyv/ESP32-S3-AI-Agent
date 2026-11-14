from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .database import SessionLocal
from .guard import MAX_HISTORY_CHARS, BudgetExceeded, trim_history
from .memory import build_memory_prompt, distill_background
from .models import Conversation, Device, Message, User, utcnow
from .protocol import TYPE_DOWNLINK, TYPE_UPLINK, ProtocolError, pack_frame, parse_frame
from .providers import (
    DialogueProvider,
    dialogue_from_profile,
    split_sentences,
    tts_pcm,
)
from .security import hash_token


logger = logging.getLogger(__name__)


@dataclass
class VoiceSession:
    id: int
    sample_rate: int
    pcm: bytearray = field(default_factory=bytearray)
    accepting: bool = True
    task: asyncio.Task | None = None
    down_sequence: int = 0
    audio_started: bool = False
    # 设备本地保存的历史，start 时一并带上来；为空才回退服务器的历史表。
    history: list[dict[str, str]] = field(default_factory=list)


class OnlineDevices:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}
        self.sessions: dict[str, "DeviceConnection"] = {}

    async def register(
        self, device_id: str, websocket: WebSocket, connection: "DeviceConnection"
    ) -> None:
        previous = self.connections.get(device_id)
        self.connections[device_id] = websocket
        self.sessions[device_id] = connection
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=4409, reason="设备在其他连接上线")
            except RuntimeError:
                pass
        self.set_online(device_id, True)

    def is_current(self, device_id: str, websocket: WebSocket) -> bool:
        return self.connections.get(device_id) is websocket

    def unregister(self, device_id: str, websocket: WebSocket) -> None:
        if self.is_current(device_id, websocket):
            self.connections.pop(device_id, None)
            self.sessions.pop(device_id, None)
            self.set_online(device_id, False)

    @staticmethod
    def set_online(device_id: str, online: bool) -> None:
        with SessionLocal() as db:
            device = db.get(Device, device_id)
            if device is not None:
                device.online = online
                device.last_seen_at = utcnow()
                db.commit()


class DeviceConnection:
    def __init__(self, websocket: WebSocket, device: Device, provider: DialogueProvider):
        self.websocket = websocket
        self.device_id = device.id
        self.user_id = device.user_id
        self.provider = provider
        self.settings = websocket.app.state.settings
        self.online: OnlineDevices = websocket.app.state.online_devices
        self.profiles = websocket.app.state.profiles
        self.panels = websocket.app.state.panels
        self.hello_received = False
        self.panel_auto = False
        self.active: VoiceSession | None = None
        self.cancelled_sessions: set[int] = set()
        self.send_lock = asyncio.Lock()
        self.guard = websocket.app.state.guard

    async def send_json(
        self, session: int, event_type: str, state: VoiceSession | None = None, **payload
    ) -> None:
        async with self.send_lock:
            if state is not None and self.active is not state:
                raise asyncio.CancelledError
            message = {"v": 1, "t": event_type, "session": session, **payload}
            await self.websocket.send_text(json.dumps(message, ensure_ascii=False))

    async def error(self, session: int, code: str, message: str) -> None:
        await self.send_json(session, "error", code=code, message=message)

    def ensure_active(self, state: VoiceSession) -> None:
        if self.active is not state:
            raise asyncio.CancelledError

    async def send_auto_emotion(self, state: VoiceSession, provider: DialogueProvider) -> None:
        if not self.panel_auto:
            return
        pop_emotion = getattr(provider.llm, "pop_emotion", None)
        if not callable(pop_emotion):
            return
        emotion = pop_emotion()
        if emotion is None:
            return
        try:
            self.ensure_active(state)
            await self.send_json(
                state.id,
                "panel",
                state,
                emotion=emotion,
                auto=True,
                ack=False,
                ttl_ms=90000,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "自动表情下发失败，继续语音：device=%s error=%s",
                self.device_id,
                type(exc).__name__,
            )

    async def send_audio(self, state: VoiceSession, pcm: bytes) -> None:
        self.ensure_active(state)
        if not state.audio_started:
            await self.send_json(
                state.id,
                "audio_start",
                state,
                sample_rate=16000,
                format="pcm_s16le",
            )
            state.audio_started = True
        frame_bytes = self.settings.audio_frame_bytes
        for offset in range(0, len(pcm), frame_bytes):
            self.ensure_active(state)
            payload = pcm[offset : offset + frame_bytes]
            # 设备下行协议只接受完整 20ms 帧，尾帧补静音，避免末尾被静默丢弃。
            payload = payload.ljust(frame_bytes, b"\x00")
            await self.websocket.send_bytes(
                pack_frame(TYPE_DOWNLINK, state.id, state.down_sequence, payload)
            )
            state.down_sequence += 1

    async def finish_audio(self, state: VoiceSession) -> None:
        if state.audio_started:
            await self.send_json(state.id, "audio_end", state)

    async def run(self) -> None:
        await self.online.register(self.device_id, self.websocket, self)
        try:
            while True:
                message = await self.websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self.handle_binary(message["bytes"])
                elif message.get("text") is not None:
                    await self.handle_text(message["text"])
        except WebSocketDisconnect:
            pass
        finally:
            state = self.active
            self.active = None
            if state is not None and state.task is not None:
                state.task.cancel()
            self.online.unregister(self.device_id, self.websocket)

    async def handle_text(self, raw: str) -> None:
        session = 0
        try:
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("v") != 1:
                raise ValueError("invalid_version")
            event_type = message.get("t")
            if not isinstance(event_type, str):
                raise ValueError("invalid_message")
            session = message.get(
                "session", 0 if event_type in {"hello", "ping", "config_ack", "history", "panel_ack"} else None
            )
            if (
                not isinstance(session, int)
                or isinstance(session, bool)
                or not 0 <= session <= 0xFFFFFFFF
            ):
                raise ValueError("invalid_session")
            if event_type == "hello":
                await self.handle_hello(message)
            elif event_type == "start":
                await self.handle_start(session, message)
            elif event_type == "end":
                await self.handle_end(session)
            elif event_type == "abort":
                self.handle_abort(session)
            elif event_type == "ping":
                await self.send_json(session, "pong")
            elif event_type == "config_ack":
                self.handle_config_ack(message)
            elif event_type == "history":
                self.handle_history(message)
            elif event_type == "panel_ack":
                self.handle_panel_ack(message)
            else:
                await self.error(session, "unknown_type", "未知消息类型")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            code = str(exc) if str(exc) in {"invalid_version", "invalid_session", "invalid_message"} else "invalid_json"
            await self.error(session, code, "控制消息格式无效")

    def handle_config_ack(self, message: dict) -> None:
        version = message.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            version = 0
        ok = bool(message.get("ok"))
        error = message.get("error", "")
        self.profiles.resolve_config(
            self.device_id, version, ok, str(error)[:200] if not ok else ""
        )

    def handle_panel_ack(self, message: dict) -> None:
        volume = message.get("volume")
        if not isinstance(volume, int) or isinstance(volume, bool):
            volume = None
        ok = bool(message.get("ok"))
        error = str(message.get("error", ""))[:200] if not ok else ""
        self.panels.resolve(self.device_id, ok, volume, error)

    def handle_history(self, message: dict) -> None:
        raw = message.get("items")
        items: list[dict] = []
        if isinstance(raw, list):
            for entry in raw[:40]:
                if not isinstance(entry, dict):
                    continue
                role = entry.get("role")
                content = entry.get("content")
                if role not in {"user", "assistant"} or not isinstance(content, str):
                    continue
                items.append({"role": role, "content": content[:4000]})
        self.profiles.resolve_history(self.device_id, items)

    async def handle_hello(self, message: dict) -> None:
        if self.active is not None:
            await self.error(self.active.id, "session_active", "语音会话中不能重复 hello")
            return
        if message.get("device_id") != self.device_id:
            await self.error(0, "device_mismatch", "hello 的 device_id 与认证设备不符")
            await self.websocket.close(code=4403)
            return
        fw_version = message.get("fw_version", "")
        protocol = message.get("protocol", "1")
        hardware = message.get("hardware", "esp32-s3")
        if not all(isinstance(value, str) for value in (fw_version, protocol, hardware)):
            await self.error(0, "invalid_hello", "hello 元数据必须为字符串")
            return
        with SessionLocal() as db:
            device = db.get(Device, self.device_id)
            if device is None:
                await self.websocket.close(code=4401)
                return
            device.fw_version = fw_version[:64]
            device.protocol = protocol[:16]
            device.hardware = hardware[:64]
            device.last_seen_at = utcnow()
            db.commit()
        self.hello_received = True
        self.panel_auto = message.get("panel_auto") is True
        volume = message.get("volume")
        if isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume <= 100:
            # 设备自己记着音量（NVS），上线时报一次，手机端读到的就是真实值。
            self.panels.remember_volume(self.device_id, volume)
        await self.send_json(0, "hello", protocol=1)
        # 离线期间暂存的配置在设备上线后自动补发一次。
        profile = self.profiles.get(self.device_id)
        if profile is not None:
            await self.send_json(
                0, "config", version=profile.version, bundle=profile.bundle
            )

    def parse_history(self, raw) -> list[dict[str, str]]:
        """start 携带的历史。围栏：条数与总字符超限时从最旧一侧丢弃，保留最新上下文。"""
        items: list[dict[str, str]] = []
        if not isinstance(raw, list):
            return items
        for entry in raw[:40]:
            if not isinstance(entry, dict):
                continue
            role = entry.get("role")
            content = entry.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                continue
            items.append({"role": role, "content": content[:4000]})
        return trim_history(
            items, self.settings.llm_history_messages, MAX_HISTORY_CHARS
        )

    async def handle_start(self, session: int, message: dict) -> None:
        if not self.hello_received:
            await self.error(session, "hello_required", "start 前必须发送 hello")
            return
        if message.get("sample_rate") != 16000:
            await self.error(session, "invalid_sample_rate", "sample_rate 必须为 16000")
            return
        history = message.get("history", [])
        if history is not None and not isinstance(history, list):
            await self.error(session, "invalid_history", "history 必须是消息数组")
            return
        if self.active is not None:
            await self.error(session, "session_active", "当前语音会话尚未结束")
            return
        self.cancelled_sessions.discard(session)
        self.active = VoiceSession(session, 16000, history=self.parse_history(history))
        logger.info("voice start received: device=%s session=%s", self.device_id, session)

    async def handle_binary(self, data: bytes) -> None:
        state = self.active
        if state is None or not state.accepting:
            return
        try:
            frame = parse_frame(data, expected_type=TYPE_UPLINK)
            if frame.session != state.id:
                await self.error(state.id, "session_mismatch", "音频 session 与当前会话不符")
                return
            if len(state.pcm) + len(frame.payload) > self.settings.max_audio_bytes:
                self.active = None
                state.accepting = False
                self.cancelled_sessions.add(state.id)
                await self.error(state.id, "audio_too_large", "录音超过服务端大小限制")
                return
            state.pcm.extend(frame.payload)
        except ProtocolError as exc:
            await self.error(state.id, exc.code, exc.message)

    async def handle_end(self, session: int) -> None:
        state = self.active
        if state is None:
            if session not in self.cancelled_sessions:
                await self.error(session, "no_active_session", "没有可结束的语音会话")
            return
        if state.id != session or not state.accepting:
            await self.error(session, "session_mismatch", "end 的 session 与当前会话不符")
            return
        state.accepting = False
        state.task = asyncio.create_task(self.process(state))

    def handle_abort(self, session: int) -> None:
        state = self.active
        if state is None or state.id != session:
            return
        self.active = None
        state.accepting = False
        self.cancelled_sessions.add(session)
        if len(self.cancelled_sessions) > 128:
            self.cancelled_sessions.pop()
        if state.task is not None:
            state.task.cancel()

    def create_conversation(self, db, state: VoiceSession) -> Conversation:
        conversation = Conversation(
            user_id=self.user_id,
            device_id=self.device_id,
            session=state.id,
            started_at=utcnow(),
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation

    @staticmethod
    def load_history(db, conversation: Conversation, limit: int) -> list[dict[str, str]]:
        rows = list(
            db.scalars(
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.user_id == conversation.user_id,
                    Conversation.device_id == conversation.device_id,
                    Conversation.id != conversation.id,
                )
                .order_by(Message.id.desc())
                .limit(limit)
            )
        )
        rows.reverse()
        return trim_history(
            [{"role": row.role, "content": row.content} for row in rows],
            limit,
            MAX_HISTORY_CHARS,
        )

    async def profile_provider(self):
        """管理员固定走小綾本体；普通用户的自有配置优先于默认路由。"""
        if self.provider.mode == "xiaoling":
            with SessionLocal() as db:
                user = db.get(User, self.user_id)
                is_admin = user is not None and user.is_admin
            if is_admin:
                return await self.websocket.app.state.dialogue_router.resolve(
                    self.user_id, self.provider
                )
        profile = self.profiles.get(self.device_id)
        if profile is not None:
            built = dialogue_from_profile(profile.bundle, self.settings)
            if built is not None:
                return built
        return await self.websocket.app.state.dialogue_router.resolve(
            self.user_id, self.provider
        )

    async def process(self, state: VoiceSession) -> None:
        provider = self.provider
        billable = provider.mode != "echo"
        budget_key = f"user:{self.user_id}"
        pending_tts: list[asyncio.Task[bytes]] = []
        try:
            provider = await self.profile_provider()
            # 成本围栏：路由准备完成后、任何 ASR/LLM/TTS 调用之前强制检查。
            billable = provider.mode != "echo"
            if billable:
                self.guard.check(budget_key)
            with SessionLocal() as db:
                conversation = self.create_conversation(db, state)
                if provider.mode == "echo":
                    if not state.pcm:
                        raise ValueError("录音为空")
                    await self.send_audio(state, bytes(state.pcm))
                    await self.finish_audio(state)
                    answer = ""
                else:
                    # 设备随 start 带来本地历史时优先使用；未带才回退服务器历史表。
                    history = state.history or self.load_history(
                        db, conversation, self.settings.llm_history_messages
                    )
                    text = (await provider.asr.transcribe(bytes(state.pcm), 16000)).strip()
                    self.ensure_active(state)
                    await self.send_json(state.id, "asr_result", state, text=text)
                    if not text:
                        answer = "没有听清，请再说一次。"
                        await self.send_json(state.id, "llm_delta", state, text=answer)
                        await self.send_audio(state, await tts_pcm(provider, answer))
                        await self.finish_audio(state)
                        conversation.ended_at = utcnow()
                        db.commit()
                        await self.send_json(state.id, "done", state, text=answer)
                        return
                    db.add(Message(conversation_id=conversation.id, role="user", content=text))
                    db.commit()

                    # 记忆注入：按 user_id 取相关记忆拼进 system prompt
                    memory_prompt = build_memory_prompt(self.user_id, text)

                    pieces: list[str] = []
                    buffer = ""
                    async for piece in provider.llm.stream(text, history, extra_system=memory_prompt):
                        self.ensure_active(state)
                        await self.send_auto_emotion(state, provider)
                        pieces.append(piece)
                        buffer += piece
                        await self.send_json(state.id, "llm_delta", state, text=piece)
                        sentences, buffer = split_sentences(buffer)
                        for sentence in sentences:
                            # 两句并行预取、按原顺序发送，避免多句回复的 TTS 串行等待。
                            pending_tts.append(asyncio.create_task(tts_pcm(provider, sentence)))
                            if len(pending_tts) >= 2:
                                pcm = await pending_tts[0]
                                pending_tts.pop(0)
                                self.ensure_active(state)
                                await self.send_audio(state, pcm)
                    # 情绪可能在最后一段正文之后到达，流结束时再补一次下发机会。
                    await self.send_auto_emotion(state, provider)
                    if buffer.strip():
                        pending_tts.append(asyncio.create_task(tts_pcm(provider, buffer.strip())))
                    while pending_tts:
                        pcm = await pending_tts[0]
                        pending_tts.pop(0)
                        self.ensure_active(state)
                        await self.send_audio(state, pcm)
                    await self.finish_audio(state)
                    answer = "".join(pieces)
                    db.add(
                        Message(
                            conversation_id=conversation.id,
                            role="assistant",
                            content=answer,
                        )
                    )
                conversation.ended_at = utcnow()
                db.commit()
                await self.send_json(state.id, "done", state, text=answer)

                if billable:
                    used = (
                        len(text)
                        + len(answer)
                        + sum(len(item["content"]) for item in history)
                        + len(memory_prompt or "")
                    )
                    self.guard.record_success(budget_key, used)

                # 蒸馏：回复后 fire-and-forget 提炼新事实（echo 模式跳过）
                if provider.mode != "echo" and text and answer:
                    asyncio.create_task(
                        distill_background(
                            provider.memory_llm,
                            text,
                            answer,
                            self.user_id,
                            guard=self.guard,
                            budget_key=budget_key,
                        )
                    )
        except asyncio.CancelledError:
            raise
        except BudgetExceeded as exc:
            logger.warning("成本围栏拦截设备 %s 的对话：%s", self.device_id, exc)
            if self.active is state:
                await self.error(state.id, "budget_exceeded", str(exc)[:200])
        except Exception as exc:
            logger.error("dialogue failed for device %s: %s", self.device_id, type(exc).__name__)
            if billable:
                self.guard.record_failure(budget_key)
            if self.active is state:
                await self.error(state.id, "dialogue_failed", str(exc)[:300])
        finally:
            for task in pending_tts:
                task.cancel()
            if pending_tts:
                await asyncio.gather(*pending_tts, return_exceptions=True)
            if self.active is state:
                self.active = None


async def device_websocket(websocket: WebSocket) -> None:
    # 设备令牌优先级：Sec-WebSocket-Protocol（fw ≥1.0.2，cpolar 透传）> Authorization Bearer（fw 1.0.1，仅直连/HTTP 隧道可用）> query token（fw ≤1.0.0 兼容）。
    # 任何分支都不记录令牌或完整 URI，避免令牌进入 access log。
    # accept() 不传 subprotocol，不回显令牌。
    token = ""
    for part in websocket.headers.get("sec-websocket-protocol", "").split(","):
        part = part.strip()
        if part.startswith("bearer."):
            token = part[len("bearer."):]
            break
    if not token:
        authorization = websocket.headers.get("authorization", "")
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            token = value.strip()
    if not token:
        token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4401, reason="缺少设备令牌")
        return
    with SessionLocal() as db:
        device = db.scalar(select(Device).where(Device.device_token_hash == hash_token(token)))
        if device is None:
            await websocket.close(code=4401, reason="设备令牌无效")
            return
        device_id = device.id
        user_id = device.user_id
    await websocket.accept()
    connection = DeviceConnection(
        websocket,
        Device(id=device_id, user_id=user_id, device_token_hash=""),
        websocket.app.state.dialogue,
    )
    await connection.run()
