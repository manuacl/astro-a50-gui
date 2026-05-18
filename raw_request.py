"""Raw HID helpers for opcodes that aren't in eh_fifty's public API.

These opcodes were observed via USB sniff of Astro Command Center on Windows.
Version format used by ACC: "<major u32 LE>.<minor u8>"
  * Base firmware major: bytes [21:25] of GET_DEVICE_INFO response
  * Base firmware minor: GET_BASE_FW_MINOR response (1 byte)
  * Headset firmware major: GET_HEADSET_FW_MAJOR response (4 bytes LE)
  * Headset firmware minor: GET_HEADSET_FW_MINOR (arg 0x0a) response (1 byte)
"""
from eh_fifty import Device


_OP_DEVICE_INFO = 0x03
_OP_FIRMWARE_INFO = 0x83
_OP_BASE_FW_MINOR = 0x55
_OP_HEADSET_FW_MAJOR = 0xDA
_OP_HEADSET_FW_MINOR = 0xD6


def _raw_request(device: Device, opcode: int, payload: bytes = b"") -> bytes:
    """Issue a raw HID request bypassing eh_fifty's _CommandType whitelist.

    Returns the response payload (bytes after the [0x02, status, len] header).
    """
    req = bytes([0x02, opcode])
    if payload:
        req += bytes([len(payload)]) + payload
    device._dev.write(0x05, req, 3000)
    resp = bytes(device._dev.read(0x85, 64, 3000))
    if not resp or resp[0] != 0x02 or len(resp) < 3:
        raise ValueError(f"unexpected response: {resp.hex()}")
    length = min(resp[2], len(resp) - 3)
    return resp[3:3 + length]


def _bcd(b: int) -> int:
    return (b >> 4) * 10 + (b & 0xF)


def _decode_datetime(buf: bytes) -> str:
    """Decode an 8-byte timestamp: u16 LE year + BCD month/day/h/m/s + pad."""
    if len(buf) < 7:
        return "?"
    year = int.from_bytes(buf[0:2], "little")
    return (f"{year:04d}-{_bcd(buf[2]):02d}-{_bcd(buf[3]):02d} "
            f"{_bcd(buf[4]):02d}:{_bcd(buf[5]):02d}:{_bcd(buf[6]):02d}")
