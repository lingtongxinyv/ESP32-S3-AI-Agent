from __future__ import annotations

import pytest

from app.serial_protocol import (
    ACK,
    GET_INFO,
    INFO,
    FrameParser,
    ProtocolTimeout,
    SerialLink,
    crc16_ccitt,
    encode_frame,
)


def test_crc16_ccitt_standard_vector() -> None:
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_frame_round_trip_and_little_endian_lengths() -> None:
    payload = {"device_id": "esp32-01", "protocol": 1}
    encoded = encode_frame(INFO, payload)
    assert encoded[:2] == b"\xA5\x5A"
    assert encoded[2] == 1
    assert encoded[3] == INFO
    payload_length = len(encoded) - 8
    assert int.from_bytes(encoded[4:6], "little") == payload_length
    frames = FrameParser().feed(encoded)
    assert len(frames) == 1
    assert frames[0].frame_type == INFO
    assert frames[0].json() == payload


def test_parser_handles_every_byte_split_and_log_prefix() -> None:
    encoded = encode_frame(INFO, {"device_id": "split"})
    parser = FrameParser()
    frames = []
    for byte in b"boot: usb log\r\n" + encoded:
        frames.extend(parser.feed(bytes([byte])))
    assert len(frames) == 1
    assert frames[0].json()["device_id"] == "split"


def test_parser_handles_sticky_frames_and_bad_crc_resync() -> None:
    bad = bytearray(encode_frame(ACK, {"ok": False}))
    bad[-1] ^= 0xFF
    first = encode_frame(INFO, {"device_id": "one"})
    second = encode_frame(ACK, {"ok": True})
    frames = FrameParser().feed(bytes(bad) + first + second)
    assert [frame.frame_type for frame in frames] == [INFO, ACK]
    assert frames[0].json()["device_id"] == "one"
    assert frames[1].json()["ok"] is True


class FakeSerial:
    def __init__(self, reply_on_write: int | None = None) -> None:
        self.reply_on_write = reply_on_write
        self.writes: list[bytes] = []
        self.incoming = bytearray()
        self.port = "TEST"

    @property
    def in_waiting(self) -> int:
        return len(self.incoming)

    def reset_input_buffer(self) -> None:
        self.incoming.clear()

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        if self.reply_on_write == len(self.writes):
            self.incoming.extend(encode_frame(INFO, {"device_id": "after-retry"}))
        return len(data)

    def flush(self) -> None:
        pass

    def read(self, size: int) -> bytes:
        if not self.incoming:
            return b""
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data


def test_request_retries_timeout_then_succeeds() -> None:
    serial = FakeSerial(reply_on_write=2)
    link = SerialLink(serial, timeout=0.002, retries=3)
    info = link.get_info()
    assert info["device_id"] == "after-retry"
    assert len(serial.writes) == 2
    assert all(write[3] == GET_INFO for write in serial.writes)


def test_request_stops_after_three_timeouts() -> None:
    serial = FakeSerial()
    link = SerialLink(serial, timeout=0.002, retries=3)
    with pytest.raises(ProtocolTimeout, match="3 次"):
        link.get_info()
    assert len(serial.writes) == 3
