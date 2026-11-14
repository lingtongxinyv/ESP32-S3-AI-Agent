from __future__ import annotations

import re
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pypinyin import Style, lazy_pinyin


DEVICE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
_CHINESE_CHARACTER_PATTERN = re.compile(
    r"[\u3007\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]"
)
_PINYIN_SYLLABLE_PATTERN = re.compile(r"[a-z]+", flags=re.ASCII)
_PINYIN_COMMAND_PATTERN = re.compile(r"[a-z]+(?: [a-z]+)*", flags=re.ASCII)


def wake_word_to_command(wake_word: str) -> str:
    """把中文唤醒短语转成 MultiNet 使用的小写无声调拼音命令。"""
    for character in wake_word:
        if _CHINESE_CHARACTER_PATTERN.fullmatch(character) is None:
            raise ValueError(f"唤醒短语含非中文字符 {character!r}，仅支持可转换的中文汉字")
    converted = lazy_pinyin(wake_word, style=Style.NORMAL, errors="default")
    if not converted:
        raise ValueError("唤醒短语的转换结果为空拼音")
    if len(converted) != len(wake_word):
        raise ValueError("拼音音节数量与汉字数量不一致")
    syllables = []
    for character, result in zip(wake_word, converted, strict=True):
        if not result:
            raise ValueError(f"汉字 {character!r} 的转换结果为空拼音")
        syllable = result.lower().replace("ü", "v")
        if _PINYIN_SYLLABLE_PATTERN.fullmatch(syllable) is None:
            raise ValueError(f"汉字 {character!r} 的转换结果是非法音节 {result!r}")
        syllables.append(syllable)
    command = " ".join(syllables)
    if not command:
        raise ValueError("唤醒短语的转换结果为空拼音")
    if _PINYIN_COMMAND_PATTERN.fullmatch(command) is None:
        raise ValueError("拼音命令格式非法，仅允许 ASCII 小写音节并以单个空格分隔")
    return command


ItemT = TypeVar("ItemT")


class ItemsResponse(BaseModel, Generic[ItemT]):
    """所有列表接口统一返回 {"items": [...]}，便于后续加分页字段。"""

    items: list[ItemT]


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: UserResponse


class PairingRequest(BaseModel):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)


class PairingResponse(BaseModel):
    pairing_token: str
    expires_at: datetime


class ClaimRequest(BaseModel):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    pairing_token: str = Field(min_length=1, max_length=512)


class ClaimResponse(BaseModel):
    device_id: str
    device_token: str


class NetworkAuthorizationRequest(BaseModel):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    pairing_token: str = Field(min_length=1, max_length=512)
    device_token: str = Field(min_length=1, max_length=512)


class NetworkAuthorizationResponse(BaseModel):
    device_id: str
    ok: bool


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device_id: str
    name: str
    hardware: str
    fw_version: str
    protocol: str
    online: bool
    claimed_at: datetime
    last_seen_at: datetime


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    session: int
    started_at: datetime
    ended_at: datetime | None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    created_at: datetime


class FirmwareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    hardware: str
    protocol: str
    notes: str
    sha256: str
    size: int
    created_at: datetime
    published_at: datetime | None


class OTAManifest(BaseModel):
    """设备侧 OTA 清单：扁平结构，固件直接读取顶层字段。"""

    device_id: str
    current_version: str
    update_available: bool
    firmware_id: int | None = None
    version: str | None = None
    hardware: str | None = None
    protocol: str | None = None
    notes: str | None = None
    size: int | None = None
    sha256: str | None = None
    download_url: str | None = None


class OTARequest(BaseModel):
    firmware_id: int


class OTAResultRequest(BaseModel):
    firmware_id: int
    status: Literal["success", "failed"]
    detail: str = Field(default="", max_length=1000)


class OTAEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    firmware_id: int
    fw_version: str = ""
    event: str
    status: str
    detail: str
    created_at: datetime


class ServiceProfile(BaseModel):
    """一路服务商的配置，统一按 OpenAI 兼容协议填写。"""

    base_url: str = Field(min_length=1, max_length=300)
    model: str = Field(default="", max_length=120)
    api_key: str = Field(min_length=1, max_length=400)
    voice: str | None = Field(default=None, max_length=120)
    speed: float = Field(default=1.0, ge=0.25, le=4.0)


class ProfilePayload(BaseModel):
    """客户端生成的配置包，服务器只放内存，不落盘。"""

    version: int = Field(default=1, ge=1, le=1_000_000)
    persona: str = Field(default="", max_length=4000)
    wake_word: str = Field(default="小绫小绫", min_length=1, max_length=24)
    llm: ServiceProfile
    asr: ServiceProfile
    tts: ServiceProfile

    @field_validator("wake_word")
    @classmethod
    def normalize_wake_word(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("唤醒词不能为空")
        wake_word_to_command(normalized)
        return normalized


class ProfileStatusResponse(BaseModel):
    device_id: str
    online: bool
    delivered: bool
    version: int | None = None
    error: str = ""


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class HistoryPullResponse(BaseModel):
    device_id: str
    items: list[HistoryItem]


class PanelCommand(BaseModel):
    """手机端下发的屏幕/音量指令；表情由固件按屏幕型号渲染。"""

    volume: int | None = Field(default=None, ge=0, le=100)
    emotion: Literal[
        "neutral", "happy", "sad", "angry", "surprised", "thinking", "sleepy", "love"
    ] | None = None
    text: str | None = Field(default=None, max_length=21)
    clear: bool = False


class PanelStatusResponse(BaseModel):
    device_id: str
    online: bool
    delivered: bool
    volume: int
    emotion: str
    text: str
    active: bool
    error: str = ""
