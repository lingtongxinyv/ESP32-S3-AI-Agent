from __future__ import annotations

import hmac
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .providers import decode_to_pcm, wav_bytes


router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


def require_service_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """供小綾插件等内部服务调用的静态令牌，与设备令牌、用户 JWT 相互独立。"""
    expected = request.app.state.settings.voice_service_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "语音服务未启用")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少服务令牌")
    if not hmac.compare_digest(authorization[7:].strip(), expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "服务令牌无效")


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    format: Literal["wav", "pcm"] = "wav"


@router.post("/asr")
async def speech_to_text(
    request: Request,
    file: UploadFile = File(...),
    format: Literal["wav", "mp3", "pcm"] = "wav",
    sample_rate: int = 16000,
    _token: None = Depends(require_service_token),
):
    settings = request.app.state.settings
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "音频为空")
    if len(data) > settings.max_audio_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "音频超过大小限制")
    if sample_rate != 16000 and format != "pcm":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非 PCM 输入统一按 16 kHz 输出")
    try:
        pcm = data if format == "pcm" else await decode_to_pcm(data)
        text = await request.app.state.dialogue.asr.transcribe(pcm, 16000)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"text": text, "chars": len(text), "audio_bytes": len(pcm)}


@router.post("/tts")
async def text_to_speech(
    payload: TTSRequest,
    request: Request,
    _token: None = Depends(require_service_token),
):
    try:
        pcm = await request.app.state.dialogue.tts.synth(payload.text)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    headers = {"X-Sample-Rate": "16000", "X-Channels": "1", "X-Format": "s16le"}
    if payload.format == "pcm":
        return Response(pcm, media_type="audio/L16", headers=headers)
    return Response(
        wav_bytes(pcm), media_type="audio/wav", headers={**headers, "X-Format": "wav"}
    )
