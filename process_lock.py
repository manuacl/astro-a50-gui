"""Single-instance helper: detect and terminate prior copies of the GUI."""
import ctypes
import os
import signal
import time
from contextlib import suppress
from pathlib import Path


PROCESS_NAME = "astro-a50-gui"
PID_FILE = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / f"{PROCESS_NAME}.pid"


def _set_process_name(name: str = PROCESS_NAME) -> None:
    # PR_SET_NAME=15; kernel truncates to TASK_COMM_LEN-1 = 15 chars.
    with suppress(OSError):
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(15, name.encode()[:15], 0, 0, 0)


def _is_our_instance(pid_dir: Path, script_path: Path) -> bool:
    # Primary check: kernel comm set via prctl matches our process name.
    try:
        if (pid_dir / "comm").read_text().strip() == PROCESS_NAME:
            return True
    except OSError:
        return False
    # Fallback: same script path running under a Python interpreter (handles
    # legacy instances launched before prctl was added).
    try:
        exe = os.readlink(pid_dir / "exe")
    except OSError:
        return False
    if "python" not in Path(exe).name.lower():
        return False
    try:
        cmdline = (pid_dir / "cmdline").read_bytes().split(b"\x00")
    except OSError:
        return False
    for arg in cmdline[1:]:
        if not arg:
            continue
        try:
            decoded = arg.decode()
        except UnicodeDecodeError:
            continue
        if Path(decoded).name != script_path.name:
            continue
        try:
            if Path(decoded).resolve(strict=True) == script_path:
                return True
        except (OSError, RuntimeError):
            continue
    return False


def _find_other_instances(script_path: Path) -> list[int]:
    me = os.getpid()
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        if _is_our_instance(entry, script_path):
            found.append(pid)
    return found


def _wait_for_exit(pid: int, timeout_s: float = 4.0) -> bool:
    for _ in range(int(timeout_s * 10)):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def _kill_previous(script_path: Path) -> None:
    pids = _find_other_instances(script_path)
    if not pids:
        return
    for pid in pids:
        with suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
    for pid in pids:
        if not _wait_for_exit(pid):
            with suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
    time.sleep(0.3)


def _remove_pid_file():
    with suppress(OSError):
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
