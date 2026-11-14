from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from .guard import MAX_HISTORY_CHARS, MAX_HISTORY_MESSAGES, BudgetExceeded, CostGuard
from .memory import build_memory_prompt, distill_background
from .models import User
from .providers import BundleTTS, decode_to_pcm, tts_pcm, wav_bytes
from .security import get_current_user

log = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

MAX_VOICE_BYTES = 10 * 1024 * 1024


def _safe_voice_name(raw: str) -> str:
    """服务商对音色名有字符限制，这里只保留字母数字下划线，空则给默认名。"""
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", raw.strip())[:64].strip("_")
    return cleaned or "my_voice"


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    history: list[HistoryMessage] = Field(default_factory=list)

    @field_validator("history")
    @classmethod
    def _limit_history(cls, value: list[HistoryMessage]) -> list[HistoryMessage]:
        if len(value) > MAX_HISTORY_MESSAGES:
            raise ValueError(f"history 最多 {MAX_HISTORY_MESSAGES} 条")
        if sum(len(item.content) for item in value) > MAX_HISTORY_CHARS:
            raise ValueError(f"history 总长度最多 {MAX_HISTORY_CHARS} 字符")
        return value


class ChatTTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class TtsPreviewRequest(BaseModel):
    """用用户自己的 TTS 配置试听，不消耗服务器默认 key。"""

    text: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=300)
    api_key: str = Field(min_length=1, max_length=400)
    model: str = Field(default="", max_length=120)
    voice: str = Field(default="", max_length=120)
    speed: float = Field(default=1.0, ge=0.25, le=4.0)


def _history_from_json(raw: str) -> list[dict[str, str]]:
    """手机端把最近几轮上下文放在 multipart 的 history 字段里传上来。"""
    try:
        items = json.loads(raw)
        history = [HistoryMessage(**item).model_dump() for item in items]
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "history 必须是消息数组"
        ) from exc
    if len(history) > MAX_HISTORY_MESSAGES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "history 条数过多")
    if sum(len(item["content"]) for item in history) > MAX_HISTORY_CHARS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "history 总长度过长")
    return history


async def _reply(
    provider,
    text: str,
    history: list[dict[str, str]],
    extra_system: str | None = None,
) -> str:
    pieces: list[str] = []
    async for piece in provider.llm.stream(text, history, extra_system=extra_system):
        pieces.append(piece)
    return "".join(pieces).strip()


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _billable(provider) -> bool:
    """echo/mock 不调用外部服务，零成本，不进围栏。"""
    return provider.mode not in {"echo", "mock"}


def _guard(request: Request) -> CostGuard:
    return request.app.state.guard


def _budget_key(user_id: int) -> str:
    return f"user:{user_id}"


@router.post("/text")
async def chat_text(
    payload: ChatTextRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    provider = await request.app.state.dialogue_router.resolve(
        user.id, request.app.state.dialogue
    )
    guard = _guard(request)
    key = _budget_key(user.id)
    billable = _billable(provider)
    if billable:
        try:
            guard.check(key)
        except BudgetExceeded as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc

    history = [item.model_dump() for item in payload.history]

    memory_prompt = build_memory_prompt(user.id, payload.text)
    try:
        reply = await _reply(
            provider, payload.text.strip(), history, extra_system=memory_prompt
        )
    except RuntimeError as exc:
        if billable:
            guard.record_failure(key)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not reply:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "对话服务没有返回内容")

    if billable:
        used = (
            len(payload.text)
            + len(reply)
            + sum(len(item["content"]) for item in history)
            + len(memory_prompt or "")
        )
        guard.record_success(key, used)

    asyncio.create_task(
        distill_background(
            provider.memory_llm,
            payload.text.strip(),
            reply,
            user.id,
            guard=guard if billable else None,
            budget_key=key if billable else None,
        )
    )
    return {"reply": reply}


@router.post("/tts")
async def chat_tts(
    payload: ChatTTSRequest,
    request: Request,
    _user: User = Depends(get_current_user),
):
    provider = request.app.state.dialogue
    try:
        pcm = await tts_pcm(provider, payload.text)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not pcm:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "语音合成没有返回音频")
    return Response(
        wav_bytes(pcm),
        media_type="audio/wav",
        headers={"X-Sample-Rate": "16000", "X-Channels": "1", "X-Format": "wav"},
    )


@router.post("/tts_preview")
async def chat_tts_preview(
    payload: TtsPreviewRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """用用户自己的 TTS 服务商配置试听音色/语速，走成本围栏，消耗用户自有 key。"""
    guard = _guard(request)
    key = _budget_key(user.id)
    try:
        guard.check(key)
    except BudgetExceeded as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc

    tts = BundleTTS(
        payload.base_url,
        payload.api_key,
        payload.model,
        payload.voice,
        payload.speed,
    )
    try:
        pcm = await tts.synth(payload.text)
    except RuntimeError as exc:
        guard.record_failure(key)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not pcm:
        guard.record_failure(key)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "语音合成没有返回音频")
    # 试听字符开销近似：文本字数 × 5（汉字约 1 token/字）
    guard.record_success(key, len(payload.text) * 5)
    return Response(
        wav_bytes(pcm),
        media_type="audio/wav",
        headers={"X-Sample-Rate": "16000", "X-Channels": "1", "X-Format": "wav"},
    )


@router.post("/voice_upload")
async def chat_voice_upload(
    base_url: str = Form(...),
    api_key: str = Form(...),
    model: str = Form(default=""),
    name: str = Form(default="my-voice"),
    text: str = Form(default=""),
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
):
    """把用户上传的音色样本转发给服务商做音色克隆，返回可直接填入 voice 的音色标识。"""
    data = await file.read(MAX_VOICE_BYTES + 1)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "音频文件为空")
    if len(data) > MAX_VOICE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "音色样本不能超过 10MB")

    form: dict[str, str] = {"customName": _safe_voice_name(name)}
    if model:
        form["model"] = model
    if text.strip():
        form["text"] = text.strip()
    url = f"{base_url.rstrip('/')}/uploads/audio/voice"
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                data=form,
                files={
                    "file": (
                        file.filename or "voice.wav",
                        data,
                        file.content_type or "audio/wav",
                    )
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"上传音色失败：{exc}") from exc
    if response.status_code != 200:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"服务商返回 HTTP {response.status_code}：{response.text[:200]}",
        )
    try:
        uri = str(response.json().get("uri") or "")
    except ValueError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "服务商返回内容不是 JSON") from exc
    if not uri:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "服务商没有返回音色标识")
    return {"voice": uri, "name": form["customName"]}


@router.post("/audio")
async def chat_audio(
    request: Request,
    file: UploadFile = File(...),
    format: Literal["wav", "mp3", "pcm"] = "wav",
    history: str | None = Form(default=None),
    _user: User = Depends(get_current_user),
):
    """一轮完整语音对话：上传录音，拿回识别文本、回复文本和回复语音。"""
    settings = request.app.state.settings
    provider = await request.app.state.dialogue_router.resolve(
        _user.id, request.app.state.dialogue
    )
    guard = _guard(request)
    key = _budget_key(_user.id)
    billable = _billable(provider)
    if billable:
        try:
            guard.check(key)
        except BudgetExceeded as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "音频为空")
    if len(data) > settings.max_audio_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "音频超过大小限制")
    past = _history_from_json(history) if history else []

    try:
        pcm = data if format == "pcm" else await decode_to_pcm(data)
        text = (await provider.asr.transcribe(pcm, 16000)).strip()
    except RuntimeError as exc:
        if billable:
            guard.record_failure(key)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "没有识别到语音内容")

    try:
        reply = await _reply(provider, text, past)
    except RuntimeError as exc:
        if billable:
            guard.record_failure(key)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not reply:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "对话服务没有返回内容")

    try:
        answer_pcm = await tts_pcm(provider, reply)
    except RuntimeError as exc:
        if billable:
            guard.record_failure(key)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    if billable:
        used = len(text) + len(reply) + sum(len(item["content"]) for item in past)
        guard.record_success(key, used)

    return Response(
        wav_bytes(answer_pcm),
        media_type="audio/wav",
        headers={
            "X-Sample-Rate": "16000",
            "X-Channels": "1",
            "X-Format": "wav",
            # 响应头只能是 ASCII，中文用 base64(UTF-8) 传。
            "X-User-Text-B64": _b64(text),
            "X-Reply-Text-B64": _b64(reply),
        },
    )
