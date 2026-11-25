import ctypes
from ctypes import wintypes
import io
import math
import struct
import threading
import time
import wave
from typing import Dict, List, Optional, Tuple

try:
    import winsound
except ImportError:
    winsound = None


_TONE_CACHE: Dict[str, bytes] = {}
_SAMPLE_RATE = 22050
# Soft peak amplitude (~35% quieter/softer than raw winsound.Beep with smooth sine wave)
_TONE_VOLUME = 0.22


# =============================================================================
# Atomic Win32 SendInput Helpers (0px drift, <0.1ms cursor excursion, multi-monitor safe)
# =============================================================================
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_MOUSEEVENTF_ABSOLUTE = 0x8000

_BUTTON_FLAGS = {
    "left": (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP),
    "right": (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP),
    "middle": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


def get_cursor_pos_win32() -> Optional[Tuple[int, int]]:
    """Fast Win32 GetCursorPos returning physical desktop coordinates."""
    try:
        pt = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))
    except Exception:
        pass
    return None


def to_virtual_desktop_coords(x: int, y: int) -> Tuple[int, int]:
    """Convert physical pixel (x, y) to Win32 0..65535 virtual-desktop coordinates."""
    user32 = ctypes.windll.user32
    vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    vw = max(1, user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
    vh = max(1, user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
    ax = int(round((x - vx + 0.5) * 65535.0 / vw))
    ay = int(round((y - vy + 0.5) * 65535.0 / vh))
    ax = max(0, min(65535, ax))
    ay = max(0, min(65535, ay))
    return ax, ay


def _make_mouse_input(dx: int, dy: int, flags: int) -> _INPUT:
    inp = _INPUT()
    inp.type = _INPUT_MOUSE
    inp.union.mi = _MOUSEINPUT(dx, dy, 0, flags, 0, None)
    return inp


def _near_any_point(
    pos: Optional[Tuple[int, int]],
    points: Tuple[Tuple[int, int], ...],
    tol: int = 3,
) -> bool:
    if pos is None:
        return False
    px, py = pos
    for zx, zy in points:
        if abs(px - zx) <= tol and abs(py - zy) <= tol:
            return True
    return False


def _ensure_cursor_restored(
    restore_pos: Optional[Tuple[int, int]],
    avoid_points: Tuple[Tuple[int, int], ...],
) -> None:
    """Wait briefly (~0.1-2ms) for Windows RIT to apply the trailing MOVE(restore_pos).

    Prevents the next cycle/wave from calling GetCursorPos() mid-flight and accidentally
    reading a fake zone coordinate as the user's real cursor position.
    """
    if restore_pos is None or _near_any_point(restore_pos, avoid_points):
        # User's real cursor was already sitting on a zone; brief settle is enough
        time.sleep(0.001)
        return

    deadline = time.perf_counter() + 0.004
    while time.perf_counter() < deadline:
        cur = get_cursor_pos_win32()
        if cur is not None and not _near_any_point(cur, avoid_points):
            return
        time.sleep(0.0002)

    # Fallback: if Windows still hadn't moved off the zone, force a clean restore move
    ox, oy = to_virtual_desktop_coords(int(restore_pos[0]), int(restore_pos[1]))
    abs_move = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK | _MOUSEEVENTF_MOVE_NOCOALESCE
    arr = (_INPUT * 1)(_make_mouse_input(ox, oy, abs_move))
    ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(_INPUT))
    time.sleep(0.0005)


def send_atomic_zones_click(
    zones: Tuple[Tuple[int, int], ...],
    button_name: str = "left",
    restore_pos: Optional[Tuple[int, int]] = None,
) -> bool:
    """Atomically click ALL target zones in one kernel SendInput batch and restore cursor.

    Sending all zones + restore in ONE atomic call guarantees:
    1) Zero physical mouse drift between move and click.
    2) GetCursorPos() is NEVER sampled between consecutive zones (eliminating the race
       where a fake zone coordinate could overwrite the real mouse position).
    3) Post-call restore barrier ensures cursor is fully back at `restore_pos` before return.
    """
    if not zones:
        return True

    down_flag, up_flag = _BUTTON_FLAGS.get(button_name, _BUTTON_FLAGS["left"])
    abs_move = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK | _MOUSEEVENTF_MOVE_NOCOALESCE

    events: List[_INPUT] = []
    for zx, zy in zones:
        ax, ay = to_virtual_desktop_coords(int(zx), int(zy))
        events.append(_make_mouse_input(ax, ay, abs_move))
        events.append(_make_mouse_input(ax, ay, abs_move | down_flag))
        events.append(_make_mouse_input(ax, ay, abs_move | up_flag))

    if restore_pos is not None:
        ox, oy = to_virtual_desktop_coords(int(restore_pos[0]), int(restore_pos[1]))
        events.append(_make_mouse_input(ox, oy, abs_move))

    arr = (_INPUT * len(events))(*events)
    sent = ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
    _ensure_cursor_restored(restore_pos, zones)
    return sent == len(events)


def send_atomic_click_at(
    x: int,
    y: int,
    button_name: str = "left",
    restore_pos: Optional[Tuple[int, int]] = None,
) -> bool:
    """Atomically move -> click -> restore at a single point (x, y)."""
    return send_atomic_zones_click(((int(x), int(y)),), button_name, restore_pos=restore_pos)


def send_atomic_move(x: int, y: int) -> bool:
    """Move cursor via Win32 SendInput (triggers Windows 11 XAML/Taskbar PointerEntered)."""
    ax, ay = to_virtual_desktop_coords(int(x), int(y))
    abs_move = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK | _MOUSEEVENTF_MOVE_NOCOALESCE
    arr = (_INPUT * 1)(_make_mouse_input(ax, ay, abs_move))
    return ctypes.windll.user32.SendInput(1, arr, ctypes.sizeof(_INPUT)) == 1


def send_atomic_press_at(x: int, y: int, button_name: str = "left") -> bool:
    """Atomically move to exact (x, y) and press mouse button."""
    down_flag, _ = _BUTTON_FLAGS.get(button_name, _BUTTON_FLAGS["left"])
    ax, ay = to_virtual_desktop_coords(int(x), int(y))
    abs_move = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK | _MOUSEEVENTF_MOVE_NOCOALESCE
    arr = (_INPUT * 2)(
        _make_mouse_input(ax, ay, abs_move),
        _make_mouse_input(ax, ay, abs_move | down_flag),
    )
    return ctypes.windll.user32.SendInput(2, arr, ctypes.sizeof(_INPUT)) == 2


def send_atomic_release_at(
    x: int,
    y: int,
    button_name: str = "left",
    restore_pos: Optional[Tuple[int, int]] = None,
) -> bool:
    """Atomically release mouse button at exact (x, y), restore cursor, and wait for restore."""
    _, up_flag = _BUTTON_FLAGS.get(button_name, _BUTTON_FLAGS["left"])
    ax, ay = to_virtual_desktop_coords(int(x), int(y))
    abs_move = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK | _MOUSEEVENTF_MOVE_NOCOALESCE
    events = [
        _make_mouse_input(ax, ay, abs_move),
        _make_mouse_input(ax, ay, abs_move | up_flag),
    ]
    if restore_pos is not None:
        ox, oy = to_virtual_desktop_coords(int(restore_pos[0]), int(restore_pos[1]))
        events.append(_make_mouse_input(ox, oy, abs_move))
    arr = (_INPUT * len(events))(*events)
    sent = ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
    _ensure_cursor_restored(restore_pos, ((int(x), int(y)),))
    return sent == len(events)


def _build_wav_bytes(notes: List[Tuple[int, int]], volume: float = _TONE_VOLUME) -> bytes:
    """Synthesize a clean, soft sine-wave WAV chime in memory with fade-in/out envelope."""
    samples = bytearray()
    max_amp = int(32767 * max(0.02, min(1.0, volume)))
    gap_samples = int(_SAMPLE_RATE * 0.015)

    for idx, (freq, dur_ms) in enumerate(notes):
        n_samples = max(1, int(_SAMPLE_RATE * (dur_ms / 1000.0)))
        fade = max(1, min(n_samples // 4, int(_SAMPLE_RATE * 0.012)))
        for i in range(n_samples):
            env = 1.0
            if i < fade:
                env = i / fade
            elif i >= n_samples - fade:
                env = (n_samples - 1 - i) / fade
            val = int(max_amp * env * math.sin(2.0 * math.pi * freq * (i / _SAMPLE_RATE)))
            samples += struct.pack("<h", val)
        if idx < len(notes) - 1:
            samples += b"\x00\x00" * gap_samples

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(bytes(samples))
    return buf.getvalue()


def get_foreground_window_title() -> str:
    """Return the title of the currently active Windows foreground window."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:
        return ""


def list_open_windows(exclude_substrings: Tuple[str, ...] = ("APEXCLICK", "NEON CLICKER")) -> List[str]:
    """Return sorted titles of visible top-level windows for the active-window filter."""
    titles: List[str] = []
    seen = set()
    try:
        user32 = ctypes.windll.user32
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if not title:
                return True
            upper = title.upper()
            if any(ex.upper() in upper for ex in exclude_substrings):
                return True
            if title not in seen:
                seen.add(title)
                titles.append(title)
            return True

        user32.EnumWindows(EnumWindowsProc(_cb), 0)
    except Exception:
        pass
    titles.sort(key=str.lower)
    return titles


def is_in_failsafe_corner(pos: Optional[Tuple[int, int]], margin: int = 5) -> bool:
    """Return True when real cursor enters top-left emergency stop corner."""
    if pos is None:
        return False
    return int(pos[0]) <= margin and int(pos[1]) <= margin


def play_tone(kind: str, enabled: bool = True) -> None:
    """Play a soft, non-blocking sine-wave UI chime via winsound.PlaySound."""
    if not enabled or winsound is None:
        return

    patterns: Dict[str, List[Tuple[int, int]]] = {
        "start": [(660, 55), (880, 75)],
        "stop": [(587, 55), (440, 75)],
        "finish": [(784, 65), (1046, 105)],
        "record": [(880, 80)],
        "failsafe": [(480, 80), (360, 110)],
    }
    seq = patterns.get(kind, [(660, 60)])
    wav_data = _TONE_CACHE.get(kind)
    if wav_data is None:
        wav_data = _build_wav_bytes(seq)
        _TONE_CACHE[kind] = wav_data

    def _worker(data: bytes) -> None:
        try:
            winsound.PlaySound(data, winsound.SND_MEMORY)
        except Exception:
            pass

    threading.Thread(target=_worker, args=(wav_data,), daemon=True).start()
