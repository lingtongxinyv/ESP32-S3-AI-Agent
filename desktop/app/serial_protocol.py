from __future__ import annotations

import json
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any

import serial
from serial.tools import list_ports

MAGIC = b"\xA5\x5A"
PROTOCOL_VERSION = 1
GET_INFO = 1
INFO = 2
SET_CONFIG = 3
ACK = 4
NACK = 5
_FIXED_TAIL_SIZE = 6  # version, type, payload length (uint16 LE), CRC (uint16 LE)


class ProtocolError(RuntimeError):
    pass


class ProtocolTimeout(ProtocolError):
    pass


class DeviceNackError(ProtocolError):
    pass


class SerialPortError(RuntimeError):
    pass


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _json_bytes(payload: dict[str, Any] | bytes | None) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def encode_frame(frame_type: int, payload: dict[str, Any] | bytes | None = None) -> bytes:
    body = _json_bytes(payload)
    if len(body) > 0xFFFF:
        raise ValueError("串口 payload 超过 uint16 长度上限")
    header = struct.pack("<BBH", PROTOCOL_VERSION, frame_type, len(body))
    crc = crc16_ccitt(header + body)
    return MAGIC + header + body + struct.pack("<H", crc)


@dataclass(frozen=True)
class Frame:
    frame_type: int
    payload: bytes

    def json(self) -> dict[str, Any]:
        if not self.payload:
            return {}
        try:
            value = json.loads(self.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("设备返回了无效 JSON payload") from exc
        if not isinstance(value, dict):
            raise ProtocolError("设备 JSON payload 必须是对象")
        return value


class FrameParser:
    """Incremental parser which resynchronizes after logs or damaged frames."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            start = self._buffer.find(MAGIC)
            if start < 0:
                # Keep a trailing A5 because it may be the first magic byte split across reads.
                keep = 1 if self._buffer.endswith(b"\xA5") else 0
                del self._buffer[: len(self._buffer) - keep]
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < len(MAGIC) + _FIXED_TAIL_SIZE:
                break
            if self._buffer[2] != PROTOCOL_VERSION:
                del self._buffer[:2]
                continue
            payload_length = int.from_bytes(self._buffer[4:6], "little")
            total_length = len(MAGIC) + _FIXED_TAIL_SIZE + payload_length
            if len(self._buffer) < total_length:
                break
            expected_crc = int.from_bytes(self._buffer[total_length - 2 : total_length], "little")
            actual_crc = crc16_ccitt(bytes(self._buffer[2 : total_length - 2]))
            if expected_crc != actual_crc:
                del self._buffer[:2]
                continue
            frame_type = self._buffer[3]
            payload = bytes(self._buffer[6 : 6 + payload_length])
            frames.append(Frame(frame_type, payload))
            del self._buffer[:total_length]
        return frames


def describe_serial_error(port: str, error: BaseException) -> str:
    message = str(error)
    lower = message.lower()
    if "access is denied" in lower and sys.platform.startswith("win"):
        return f"无法打开串口 {port}：串口被占用或访问被拒绝，请关闭其他串口程序后重试。"
    if isinstance(error, PermissionError) or "permission denied" in lower:
        return (
            f"无法打开串口 {port}：权限不足。Linux 下请将当前用户加入 dialout 组后重新登录，"
            "并检查串口设备权限。"
        )
    if "resource busy" in lower or "device or resource busy" in lower or "being used" in lower:
        return f"无法打开串口 {port}：串口正被其他程序独占，请关闭串口监视器等程序后重试。"
    if isinstance(error, FileNotFoundError) or "no such file" in lower or "cannot find" in lower:
        return f"串口 {port} 不存在或设备已断开，请重新扫描串口。"
    return f"串口 {port} 操作失败：{message}"


def open_serial(port: str) -> serial.Serial:
    try:
        return serial.Serial(
            port=port,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
            write_timeout=2.0,
            exclusive=None if sys.platform.startswith("win") else True,
        )
    except (serial.SerialException, OSError) as exc:
        raise SerialPortError(describe_serial_error(port, exc)) from exc


def available_ports() -> list[tuple[str, str]]:
    result = []
    for info in list_ports.comports():
        label = f"{info.device} — {info.description}" if info.description else info.device
        result.append((info.device, label))
    return result


class SerialLink:
    def __init__(self, serial_port: Any, timeout: float = 2.0, retries: int = 3) -> None:
        if retries < 1:
            raise ValueError("重试次数至少为 1")
        self.serial = serial_port
        self.timeout = timeout
        self.retries = retries

    def request(
        self,
        request_type: int,
        expected_type: int,
        payload: dict[str, Any] | bytes | None = None,
    ) -> Frame:
        encoded = encode_frame(request_type, payload)
        for attempt in range(self.retries):
            parser = FrameParser()
            if hasattr(self.serial, "reset_input_buffer"):
                self.serial.reset_input_buffer()
            try:
                self.serial.write(encoded)
                if hasattr(self.serial, "flush"):
                    self.serial.flush()
            except (serial.SerialException, OSError) as exc:
                name = getattr(self.serial, "port", "未知")
                raise SerialPortError(describe_serial_error(name, exc)) from exc
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    waiting = getattr(self.serial, "in_waiting", 0)
                    chunk = self.serial.read(waiting or 1)
                except (serial.SerialException, OSError) as exc:
                    name = getattr(self.serial, "port", "未知")
                    raise SerialPortError(describe_serial_error(name, exc)) from exc
                if not chunk:
                    continue
                for frame in parser.feed(chunk):
                    if frame.frame_type == NACK:
                        detail = frame.json().get("error", "设备拒绝请求")
                        raise DeviceNackError(str(detail))
                    if frame.frame_type == expected_type:
                        return frame
        raise ProtocolTimeout(f"设备响应超时，已尝试 {self.retries} 次")

    def get_info(self) -> dict[str, Any]:
        info = self.request(GET_INFO, INFO).json()
        if not info.get("device_id"):
            raise ProtocolError("设备 INFO 响应缺少 device_id")
        return info

    def set_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return self.request(SET_CONFIG, ACK, config).json()
