"""Low-level read helpers for native DWG parsing.

The implementation intentionally contains no third-party DWG code.  It follows
the public DWG bit-code descriptions for the primitive formats needed by the
AC1015 read-only MVP.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional


class DwgBinaryReadError(ValueError):
    """Raised when a DWG byte/bit stream is malformed or truncated."""


@dataclass(frozen=True)
class DwgHandleRef:
    code: int
    counter: int
    value: int
    raw: bytes

    @property
    def hex_value(self) -> str:
        return f"{self.value:X}" if self.value else "0"


class DwgBinaryReader:
    """Bounded byte and MSB-first bit reader for DWG primitive values."""

    def __init__(self, data: bytes | bytearray | memoryview, *, offset: int = 0, length: Optional[int] = None):
        raw = memoryview(data)
        if offset < 0 or offset > len(raw):
            raise DwgBinaryReadError(f"offset {offset} is outside buffer length {len(raw)}")
        end = len(raw) if length is None else offset + length
        if end < offset or end > len(raw):
            raise DwgBinaryReadError(f"length {length} from offset {offset} is outside buffer")
        self._data = raw[offset:end]
        self._byte_pos = 0
        self._bit_pos = 0

    @property
    def size(self) -> int:
        return len(self._data)

    @property
    def byte_pos(self) -> int:
        return self._byte_pos

    @property
    def bit_pos(self) -> int:
        return self._bit_pos

    def remaining(self) -> int:
        return self.size - self._byte_pos - (1 if self._bit_pos else 0)

    def tell_bits(self) -> int:
        return self._byte_pos * 8 + self._bit_pos

    def seek(self, byte_pos: int) -> None:
        if byte_pos < 0 or byte_pos > self.size:
            raise DwgBinaryReadError(f"seek {byte_pos} is outside buffer length {self.size}")
        self._byte_pos = byte_pos
        self._bit_pos = 0

    def align_byte(self) -> None:
        if self._bit_pos:
            self._byte_pos += 1
            self._bit_pos = 0

    def read_bytes(self, count: int) -> bytes:
        self.align_byte()
        if count < 0:
            raise DwgBinaryReadError("negative read size")
        end = self._byte_pos + count
        if end > self.size:
            raise DwgBinaryReadError(f"truncated read: need {count} bytes at {self._byte_pos}")
        payload = self._data[self._byte_pos:end].tobytes()
        self._byte_pos = end
        return payload

    def peek_bytes(self, count: int) -> bytes:
        self.align_byte()
        end = self._byte_pos + count
        if end > self.size:
            raise DwgBinaryReadError(f"truncated peek: need {count} bytes at {self._byte_pos}")
        return self._data[self._byte_pos:end].tobytes()

    def read_u8(self) -> int:
        return self.read_bytes(1)[0]

    def read_i8(self) -> int:
        value = self.read_u8()
        return value - 256 if value & 0x80 else value

    def read_u16_le(self) -> int:
        return struct.unpack("<H", self.read_bytes(2))[0]

    def read_u16_be(self) -> int:
        return struct.unpack(">H", self.read_bytes(2))[0]

    def read_i16_le(self) -> int:
        return struct.unpack("<h", self.read_bytes(2))[0]

    def read_u32_le(self) -> int:
        return struct.unpack("<I", self.read_bytes(4))[0]

    def read_i32_le(self) -> int:
        return struct.unpack("<i", self.read_bytes(4))[0]

    def read_f64_le(self) -> float:
        return struct.unpack("<d", self.read_bytes(8))[0]

    def read_string_u16(self, *, encoding: str = "utf-8") -> str:
        size = self.read_u16_le()
        if size == 0:
            return ""
        return self.read_bytes(size).decode(encoding)

    def read_bit(self) -> int:
        if self._byte_pos >= self.size:
            raise DwgBinaryReadError("truncated bit read")
        byte = self._data[self._byte_pos]
        value = (byte >> (7 - self._bit_pos)) & 1
        self._bit_pos += 1
        if self._bit_pos == 8:
            self._bit_pos = 0
            self._byte_pos += 1
        return value

    def read_bits(self, count: int) -> int:
        if count < 0:
            raise DwgBinaryReadError("negative bit count")
        value = 0
        for _ in range(count):
            value = (value << 1) | self.read_bit()
        return value

    def read_bit_pair(self) -> int:
        return self.read_bits(2)

    def _read_bit_bytes_le(self, count: int) -> bytes:
        return bytes(self.read_bits(8) for _ in range(count))

    def read_bit_short(self) -> int:
        opcode = self.read_bit_pair()
        if opcode == 0:
            return struct.unpack("<h", self._read_bit_bytes_le(2))[0]
        if opcode == 1:
            return self.read_bits(8)
        if opcode == 2:
            return 0
        return 256

    def read_bit_long(self) -> int:
        opcode = self.read_bit_pair()
        if opcode == 0:
            return struct.unpack("<i", self._read_bit_bytes_le(4))[0]
        if opcode == 1:
            return self.read_bits(8)
        if opcode == 2:
            return 0
        raise DwgBinaryReadError("invalid bitlong opcode 3")

    def read_bit_double(self) -> float:
        opcode = self.read_bit_pair()
        if opcode == 0:
            return struct.unpack("<d", self._read_bit_bytes_le(8))[0]
        if opcode == 1:
            return 1.0
        if opcode == 2:
            return 0.0
        raise DwgBinaryReadError("invalid bitdouble opcode 3")

    def read_bit_double_with_default(self, default: float) -> float:
        opcode = self.read_bit_pair()
        if opcode == 0:
            return default
        default_bytes = bytearray(struct.pack("<d", float(default)))
        if opcode == 1:
            default_bytes[:4] = self._read_bit_bytes_le(4)
            return struct.unpack("<d", default_bytes)[0]
        if opcode == 2:
            patch = self._read_bit_bytes_le(6)
            default_bytes[4:6] = patch[:2]
            default_bytes[:4] = patch[2:]
            return struct.unpack("<d", default_bytes)[0]
        return struct.unpack("<d", self._read_bit_bytes_le(8))[0]

    def read_handle(self) -> DwgHandleRef:
        code = self.read_bits(4)
        counter = self.read_bits(4)
        raw = self._read_bit_bytes_le(counter)
        value = 0
        for item in raw:
            value = (value << 8) | item
        return DwgHandleRef(code=code, counter=counter, value=value, raw=raw)

    def read_modular_char(self, *, signed: bool = False) -> int:
        shift = 0
        value = 0
        negative = False
        for _ in range(8):
            byte = self.read_u8()
            payload = byte & 0x7F
            if not (byte & 0x80) and signed and (payload & 0x40):
                negative = True
                payload &= 0x3F
            value |= payload << shift
            if not (byte & 0x80):
                return -value if negative else value
            shift += 7
        raise DwgBinaryReadError("unterminated modular char")

    def read_modular_short(self, *, signed: bool = False) -> int:
        shift = 0
        value = 0
        negative = False
        for _ in range(4):
            word = self.read_u16_le()
            payload = word & 0x7FFF
            if not (word & 0x8000) and signed and (payload & 0x4000):
                negative = True
                payload &= 0x3FFF
            value |= payload << shift
            if not (word & 0x8000):
                return -value if negative else value
            shift += 15
        raise DwgBinaryReadError("unterminated modular short")
