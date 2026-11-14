from __future__ import annotations

import struct
from dataclasses import dataclass


PROTOCOL_VERSION = 1
TYPE_UPLINK = 1
TYPE_DOWNLINK = 2
HEADER = struct.Struct("<BBHIII")
HEADER_SIZE = HEADER.size


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class AudioFrame:
    version: int
    type: int
    flags: int
    session: int
    sequence: int
    payload: bytes


def pack_frame(frame_type: int, session: int, sequence: int, payload: bytes, flags: int = 0) -> bytes:
    return HEADER.pack(
        PROTOCOL_VERSION, frame_type, flags, session, sequence, len(payload)
    ) + payload


def parse_frame(data: bytes, expected_type: int | None = None) -> AudioFrame:
    if len(data) < HEADER_SIZE:
        raise ProtocolError("invalid_length", "二进制帧不足 16 字节")
    version, frame_type, flags, session, sequence, payload_bytes = HEADER.unpack_from(data)
    if version != PROTOCOL_VERSION:
        raise ProtocolError("invalid_version", "二进制协议版本必须为 1")
    if expected_type is not None and frame_type != expected_type:
        raise ProtocolError("invalid_type", "二进制音频方向不正确")
    if payload_bytes != len(data) - HEADER_SIZE:
        raise ProtocolError("length_mismatch", "二进制帧声明长度与实际长度不一致")
    if payload_bytes % 2:
        raise ProtocolError("invalid_pcm", "PCM s16le 负载长度必须为偶数")
    return AudioFrame(version, frame_type, flags, session, sequence, data[HEADER_SIZE:])
