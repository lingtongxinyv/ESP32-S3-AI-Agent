from __future__ import annotations

import struct

import pytest

from app.protocol import HEADER, TYPE_DOWNLINK, TYPE_UPLINK, ProtocolError, pack_frame, parse_frame
from tools.virtual_device import decode_audio_frame, encode_audio_frame


def test_binary_header_layout_and_validation():
    payload = b"\x01\x02\x03\x04"
    data = pack_frame(TYPE_UPLINK, 0x10203040, 7, payload, flags=3)
    assert HEADER.size == 16
    assert struct.unpack("<BBHIII", data[:16]) == (1, 1, 3, 0x10203040, 7, 4)
    frame = parse_frame(data, expected_type=TYPE_UPLINK)
    assert frame.payload == payload
    assert frame.sequence == 7

    with pytest.raises(ProtocolError):
        parse_frame(data[:10])
    with pytest.raises(ProtocolError):
        parse_frame(b"\x02" + data[1:])
    with pytest.raises(ProtocolError):
        parse_frame(data[:-1])
    with pytest.raises(ProtocolError):
        parse_frame(pack_frame(TYPE_UPLINK, 1, 0, b"x"))
    with pytest.raises(ProtocolError):
        parse_frame(data, expected_type=TYPE_DOWNLINK)


def test_virtual_device_frame_helpers_are_wire_compatible():
    pcm = b"\x00\x01\xff\xff"
    encoded = encode_audio_frame(2, 99, 4, pcm)
    assert encoded == pack_frame(TYPE_DOWNLINK, 99, 4, pcm)
    assert decode_audio_frame(encoded) == (99, 4, pcm)
