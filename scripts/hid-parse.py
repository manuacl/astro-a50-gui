#!/usr/bin/env python3
"""Parse an A50 usbmon text capture into a request/response table.

Usage:
    ./hid-parse.py <capture.txt> [--annotations annotations.txt]

The wire protocol for the A50 (cf. eh_fifty.py) is:
    request:  [0x02, opcode, len, *payload]   on EP 5 OUT
    response: [0x02, status, len, *payload]   on EP 5 IN (0x85)

In usbmon text-mode lines look like:
    URB-id ts(us) S|C Io|Ii:bus:dev:ep status_or_setup length = data_hex...

We pair each `S Io` (submission with payload) with the next `C Ii` (completed
read with payload) on the same endpoint to form a logical request/response.

Known opcodes are loaded from eh_fifty._CommandType so we can label them.
"""
import argparse
import re
import sys
from collections import deque
from pathlib import Path

# Make eh_fifty importable from the venv so we can label known opcodes.
VENV_SITE = Path(__file__).parent / ".venv" / "lib"
for site in VENV_SITE.glob("python*/site-packages"):
    sys.path.insert(0, str(site))

try:
    from eh_fifty import _CommandType  # type: ignore[attr-defined]
    KNOWN_OPCODES = {c.value: c.name for c in _CommandType}
except Exception:
    KNOWN_OPCODES = {}


# Example line:
#   ffff8f1b729cac00 1158406211 S Io:017:05 -115 2 = 0254
# usbmon text mode uses "Type:Dev:EP" (bus is implicit via /sys/.../usbmon/Nt).
LINE_RE = re.compile(
    r"^(?P<tag>\S+)\s+(?P<ts>\S+)\s+(?P<event>[SCE])\s+"
    r"(?P<type>[BCIZ][io]):(?P<dev>\d+):(?P<ep>\d+)\s+"
    r"(?P<rest>.*)$"
)


def parse_data(rest: str) -> bytes | None:
    """Extract the data bytes from the trailing `= xx xx xx ...` of a line."""
    if "=" not in rest:
        return None
    hex_part = rest.split("=", 1)[1].strip()
    hex_clean = hex_part.replace(" ", "")
    try:
        return bytes.fromhex(hex_clean)
    except ValueError:
        return None


def fmt_hex(b: bytes, max_len: int = 32) -> str:
    if not b:
        return ""
    if len(b) <= max_len:
        return b.hex()
    return b[:max_len].hex() + f"... (+{len(b)-max_len}B)"


_STATUS_NAMES = {0: "NO_RESP", 1: "ERROR", 2: "OK"}


def decode_response(resp: bytes) -> tuple[str, bytes]:
    """Decode a response frame as `status:len bytes` based on the eh_fifty format."""
    if not resp or resp[0] != 0x02 or len(resp) < 3:
        return "?", resp
    status = _STATUS_NAMES.get(resp[1], f"0x{resp[1]:02x}")
    declared_len = resp[2]
    payload = resp[3:3 + declared_len] if declared_len else b""
    return status, payload


def load_annotations(path: Path) -> list[tuple[float, str]]:
    """Optional annotations file: each line is `<timestamp_us> <label>`."""
    out: list[tuple[float, str]] = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            ts_str, label = line.split(maxsplit=1)
            out.append((float(ts_str), label))
        except ValueError:
            pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--annotations", type=Path, default=None)
    parser.add_argument("--ep-out", type=int, default=5, help="OUT endpoint number")
    parser.add_argument("--summary", action="store_true",
                        help="Print only one row per (opcode, request payload) tuple")
    args = parser.parse_args()

    annotations = sorted(load_annotations(args.annotations)) if args.annotations else []
    ann_idx = 0

    pending_requests: deque[tuple[float, int, bytes]] = deque()  # (ts, opcode, payload)
    rows: list[tuple[float, int, bytes, bytes | None]] = []

    for line in args.capture.read_text().splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        event = m.group("event")
        type_ = m.group("type")  # e.g. "Io" or "Ii"
        ep = int(m.group("ep"))
        if ep != args.ep_out:
            continue
        try:
            ts = float(m.group("ts"))
        except ValueError:
            continue
        data = parse_data(m.group("rest"))

        is_out = type_.endswith("o")
        is_in = type_.endswith("i")

        # Host->device payload travels on S(ubmission) of OUT endpoints.
        if event == "S" and is_out and data and len(data) >= 2 and data[0] == 0x02:
            opcode = data[1]
            # ACC always sends 64-byte padded buffers; eh_fifty trims to actual
            # size. Either way, byte 2 (if present) is the declared payload len.
            if len(data) >= 3:
                declared_len = data[2]
                payload = data[3:3 + declared_len]
            else:
                payload = b""
            pending_requests.append((ts, opcode, payload))
        # Device->host payload travels on C(ompletion) of IN endpoints.
        elif event == "C" and is_in and data and len(data) >= 2 and data[0] == 0x02:
            try:
                req_ts, opcode, req_payload = pending_requests.popleft()
            except IndexError:
                # Unsolicited IN packet (rare); record with sentinel opcode
                rows.append((ts, -1, b"", data))
                continue
            rows.append((req_ts, opcode, req_payload, data))

    # Optionally collapse duplicates (useful for noisy captures)
    if args.summary:
        seen: dict[tuple[int, bytes], tuple[float, bytes | None]] = {}
        for ts, opcode, req, resp in rows:
            key = (opcode, req)
            if key not in seen:
                seen[key] = (ts, resp)
        rows = [(ts, op, req, resp) for (op, req), (ts, resp) in seen.items()]
        rows.sort()

    # Print table
    print(f"{'ts(us)':>14}  {'op':>4}  {'name':27}  {'request':22}{'st':4}{'len':4}response")
    print("-" * 120)
    for ts, opcode, req, resp in rows:
        # Emit pending annotation entries that occurred before this row's ts
        while ann_idx < len(annotations) and annotations[ann_idx][0] <= ts:
            ann_ts, label = annotations[ann_idx]
            print(f"  --- {ann_ts:.0f} :: {label} ---")
            ann_idx += 1

        if opcode == -1:
            name = "(unsolicited)"
            op_str = "—"
        else:
            name = KNOWN_OPCODES.get(opcode, "??UNKNOWN??")
            op_str = f"{opcode:02x}"
        status, payload = decode_response(resp) if resp else ("", b"")
        print(f"{ts:>14.0f}  0x{op_str:>2}  {name:27}  {fmt_hex(req):22}"
              f"{status:4}{len(payload):>3} {fmt_hex(payload)}")

    # Trailing annotations (if any)
    while ann_idx < len(annotations):
        ann_ts, label = annotations[ann_idx]
        print(f"  --- {ann_ts:.0f} :: {label} ---")
        ann_idx += 1

    # Summary: which opcodes were observed?
    observed = sorted({op for _, op, _, _ in rows if op >= 0})
    known = [op for op in observed if op in KNOWN_OPCODES]
    unknown = [op for op in observed if op not in KNOWN_OPCODES]
    print()
    print(f"# {len(rows)} exchanges, {len(observed)} distinct opcodes")
    print(f"# known   ({len(known)}): {' '.join(f'{o:02x}' for o in known)}")
    print(f"# unknown ({len(unknown)}): {' '.join(f'{o:02x}' for o in unknown)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
