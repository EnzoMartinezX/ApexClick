import ctypes
from ctypes import wintypes
import io
import math
import queue
import struct
import threading
import time
import wave
from typing import Callable, Dict, List, Optional, Tuple

try:
    import winsound
except ImportError:
    winsound = None


# Dedicated WinDLL instances so argtypes/restype never collide with pynput's windll.user32
_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

_TONE_CACHE: Dict[str, bytes] = {}
_SAMPLE_RATE = 22050
# Soft peak amplitude (~35% quieter/softer than raw winsound.Beep with smooth sine wave)
_TONE_VOLUME = 0.22


# =============================================================================
# Atomic Win32 SendInput Helpers (Mouse + Hardware-VK Keyboard, Game-Safe)
# =============================================================================
_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_HWHEEL = 0x1000
_MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_MOUSEEVENTF_ABSOLUTE = 0x8000
_WHEEL_DELTA = 120

_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002

# Windows Virtual Keys that require KEYEVENTF_EXTENDEDKEY
_EXTENDED_VKS = frozenset({
    0xA3,  # VK_RCONTROL
    0xA5,  # VK_RMENU (Right Alt / AltGr)
    0x21,  # VK_PRIOR (Page Up)
    0x22,  # VK_NEXT (Page Down)
    0x23,  # VK_END
    0x24,  # VK_HOME
    0x25,  # VK_LEFT
    0x26,  # VK_UP
    0x27,  # VK_RIGHT
    0x28,  # VK_DOWN
    0x2C,  # VK_SNAPSHOT (Print Screen)
    0x2D,  # VK_INSERT
    0x2E,  # VK_DELETE
    0x5B,  # VK_LWIN
    0x5C,  # VK_RWIN
    0x5D,  # VK_APPS
    0x6F,  # VK_DIVIDE (Numpad /)
    0x90,  # VK_NUMLOCK
    0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3,  # Media keys
})

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


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


_USER32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
_USER32.SendInput.restype = wintypes.UINT
_USER32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
_USER32.MapVirtualKeyW.restype = wintypes.UINT
_USER32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
_USER32.VkKeyScanW.restype = ctypes.c_short
_USER32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
_USER32.GetCursorPos.restype = wintypes.BOOL
_USER32.GetSystemMetrics.argtypes = (ctypes.c_int,)
_USER32.GetSystemMetrics.restype = ctypes.c_int


def resolve_char_to_vk(ch: str) -> Optional[int]:
    """Resolve a single character to its base Windows Virtual Key code (ignoring Shift state)."""
    if not ch or len(ch) != 1:
        return None
    # Use lowercase base key for ASCII letters so VK is always 0x41..0x5A with no shift mismatch
    target = ch.lower() if ("A" <= ch <= "Z") else ch
    try:
        res = int(_USER32.VkKeyScanW(target))
        if res == -1:
            return None
        vk = res & 0xFF
        return vk if 1 <= vk <= 0xFE else None
    except Exception:
        return None


def send_key_vk(vk: int, is_press: bool, extended: Optional[bool] = None) -> bool:
    """Send a hardware-compatible Win32 keyboard event (wVk + wScan, NEVER KEYEVENTF_UNICODE).

    Fixes the pynput bug where releasing/pressing a letter while SHIFT is held emits
    KEYEVENTF_UNICODE (wVk=0), leaving physical keys permanently stuck down in games.
    """
    vk_int = int(vk) & 0xFF
    if vk_int == 0:
        return False
    scan = int(_USER32.MapVirtualKeyW(vk_int, 0)) & 0xFFFF  # MAPVK_VK_TO_VSC
    is_ext = (vk_int in _EXTENDED_VKS) if extended is None else bool(extended)
    flags = (_KEYEVENTF_EXTENDEDKEY if is_ext else 0) | (0 if is_press else _KEYEVENTF_KEYUP)

    inp = _INPUT()
    inp.type = _INPUT_KEYBOARD
    inp.union.ki = _KEYBDINPUT(vk_int, scan, flags, 0, None)
    arr = (_INPUT * 1)(inp)
    return _USER32.SendInput(1, arr, ctypes.sizeof(_INPUT)) == 1


def send_mouse_button(button_name: str, is_press: bool) -> bool:
    """Send a clean Win32 mouse button press or release at the current cursor position."""
    down_flag, up_flag = _BUTTON_FLAGS.get(button_name, _BUTTON_FLAGS["left"])
    flag = down_flag if is_press else up_flag
    arr = (_INPUT * 1)(_make_mouse_input(0, 0, flag))
    return _USER32.SendInput(1, arr, ctypes.sizeof(_INPUT)) == 1


def send_mouse_scroll(dx: int, dy: int) -> bool:
    """Send Win32 mouse wheel scroll via SendInput."""
    events: List[_INPUT] = []
    if dy:
        inp = _INPUT()
        inp.type = _INPUT_MOUSE
        inp.union.mi = _MOUSEINPUT(0, 0, wintypes.DWORD(int(dy * _WHEEL_DELTA) & 0xFFFFFFFF), _MOUSEEVENTF_WHEEL, 0, None)
        events.append(inp)
    if dx:
        inp = _INPUT()
        inp.type = _INPUT_MOUSE
        inp.union.mi = _MOUSEINPUT(0, 0, wintypes.DWORD(int(dx * _WHEEL_DELTA) & 0xFFFFFFFF), _MOUSEEVENTF_HWHEEL, 0, None)
        events.append(inp)
    if not events:
        return True
    arr = (_INPUT * len(events))(*events)
    return _USER32.SendInput(len(events), arr, ctypes.sizeof(_INPUT)) == len(events)


# =============================================================================
# Ultra-Fast Async Win32 WH_MOUSE_LL Hook (Never Times Out Under 1000Hz Games)
# =============================================================================
_WH_MOUSE_LL = 14
_WM_QUIT = 0x0012
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_WM_RBUTTONDOWN = 0x0204
_WM_RBUTTONUP = 0x0205
_WM_MBUTTONDOWN = 0x0207
_WM_MBUTTONUP = 0x0208
_WM_MOUSEWHEEL = 0x020A
_WM_MOUSEHWHEEL = 0x020E

_HOOK_CLICK_MAP = {
    _WM_LBUTTONDOWN: ("left", True),
    _WM_LBUTTONUP: ("left", False),
    _WM_RBUTTONDOWN: ("right", True),
    _WM_RBUTTONUP: ("right", False),
    _WM_MBUTTONDOWN: ("middle", True),
    _WM_MBUTTONUP: ("middle", False),
}


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_LRESULT = ctypes.c_ssize_t
_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_USER32.SetWindowsHookExW.argtypes = (ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
_USER32.SetWindowsHookExW.restype = wintypes.HHOOK
_USER32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
_USER32.UnhookWindowsHookEx.restype = wintypes.BOOL
_USER32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_USER32.CallNextHookEx.restype = _LRESULT
_USER32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_USER32.GetMessageW.restype = wintypes.BOOL
_USER32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
_USER32.PeekMessageW.restype = wintypes.BOOL
_USER32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_USER32.PostThreadMessageW.restype = wintypes.BOOL
_KERNEL32.GetCurrentThreadId.argtypes = ()
_KERNEL32.GetCurrentThreadId.restype = wintypes.DWORD


class FastMouseHook:
    """Async Win32 WH_MOUSE_LL hook that drops unrequested WM_MOUSEMOVE in ~1us.

    Avoids pynput's synchronous NotImplementedError-per-packet overhead that causes
    Windows 11 to silently unhook WH_MOUSE_LL during 3D games / 1000Hz mice.
    """

    def __init__(
        self,
        on_click: Optional[Callable[[int, int, str, bool, float], None]] = None,
        on_move: Optional[Callable[[int, int, float], None]] = None,
        on_scroll: Optional[Callable[[int, int, int, int, float], None]] = None,
        record_moves: bool = False,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._on_click = on_click
        self._on_move = on_move
        self._on_scroll = on_scroll
        self._record_moves = bool(record_moves and on_move is not None)
        self._clock = clock

        self._queue: "queue.SimpleQueue[Optional[Tuple[int, int, int, int, float]]]" = queue.SimpleQueue()
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._hook_tid: Optional[int] = None
        self._hook_thread: Optional[threading.Thread] = None
        self._dispatch_thread: Optional[threading.Thread] = None
        self._proc_ref = None  # prevent GC of ctypes callback while hooked

    def start(self) -> None:
        self._dispatch_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatch_thread.start()
        self._hook_thread = threading.Thread(target=self._hook_loop, daemon=True)
        self._hook_thread.start()
        self._ready.wait(timeout=1.0)

    def stop(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        tid = self._hook_tid
        if tid:
            try:
                _USER32.PostThreadMessageW(tid, _WM_QUIT, 0, 0)
            except Exception:
                pass
        self._queue.put(None)
        if self._hook_thread and self._hook_thread.is_alive():
            self._hook_thread.join(timeout=0.5)
        if self._dispatch_thread and self._dispatch_thread.is_alive():
            self._dispatch_thread.join(timeout=0.5)

    def _hook_loop(self) -> None:
        self._hook_tid = int(_KERNEL32.GetCurrentThreadId())
        # Ensure thread message queue exists before signaling ready
        msg = wintypes.MSG()
        _USER32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

        record_moves = self._record_moves
        q_put = self._queue.put
        clock = self._clock
        want_click = self._on_click is not None
        want_scroll = self._on_scroll is not None

        def _low_level_proc(code: int, wparam: int, lparam: int) -> int:
            if code == 0:
                if wparam == _WM_MOUSEMOVE:
                    if record_moves:
                        st = ctypes.cast(lparam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                        q_put((wparam, st.pt.x, st.pt.y, 0, clock()))
                elif (want_click and wparam in _HOOK_CLICK_MAP) or (
                    want_scroll and wparam in (_WM_MOUSEWHEEL, _WM_MOUSEHWHEEL)
                ):
                    st = ctypes.cast(lparam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                    q_put((wparam, st.pt.x, st.pt.y, st.mouseData, clock()))
            return _USER32.CallNextHookEx(None, code, wparam, lparam)

        self._proc_ref = _HOOKPROC(_low_level_proc)
        hook = _USER32.SetWindowsHookExW(_WH_MOUSE_LL, self._proc_ref, None, 0)
        self._ready.set()
        if not hook:
            return
        try:
            while not self._stopped.is_set():
                ret = _USER32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0 or msg.message == _WM_QUIT:
                    break
        finally:
            _USER32.UnhookWindowsHookEx(hook)
            self._proc_ref = None

    def _dispatch_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            wparam, x, y, mdata, ts = item
            try:
                if wparam == _WM_MOUSEMOVE:
                    if self._on_move is not None:
                        self._on_move(x, y, ts)
                elif wparam in _HOOK_CLICK_MAP:
                    if self._on_click is not None:
                        btn_name, pressed = _HOOK_CLICK_MAP[wparam]
                        self._on_click(x, y, btn_name, pressed, ts)
                elif wparam == _WM_MOUSEWHEEL:
                    if self._on_scroll is not None:
                        delta = ctypes.c_short((mdata >> 16) & 0xFFFF).value // _WHEEL_DELTA
                        if delta:
                            self._on_scroll(x, y, 0, delta, ts)
                elif wparam == _WM_MOUSEHWHEEL:
                    if self._on_scroll is not None:
                        delta = ctypes.c_short((mdata >> 16) & 0xFFFF).value // _WHEEL_DELTA
                        if delta:
                            self._on_scroll(x, y, delta, 0, ts)
            except Exception:
                pass


def get_cursor_pos_win32() -> Optional[Tuple[int, int]]:
    """Fast Win32 GetCursorPos returning physical desktop coordinates."""
    try:
        pt = wintypes.POINT()
        if _USER32.GetCursorPos(ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))
    except Exception:
        pass
    return None


def to_virtual_desktop_coords(x: int, y: int) -> Tuple[int, int]:
    """Convert physical pixel (x, y) to Win32 0..65535 virtual-desktop coordinates."""
    vx = _USER32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    vy = _USER32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    vw = max(1, _USER32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
    vh = max(1, _USER32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
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
    _USER32.SendInput(1, arr, ctypes.sizeof(_INPUT))
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
    sent = _USER32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
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
    return _USER32.SendInput(1, arr, ctypes.sizeof(_INPUT)) == 1


def send_atomic_press_at(x: int, y: int, button_name: str = "left") -> bool:
    """Atomically move to exact (x, y) and press mouse button."""
    down_flag, _ = _BUTTON_FLAGS.get(button_name, _BUTTON_FLAGS["left"])
    ax, ay = to_virtual_desktop_coords(int(x), int(y))
    abs_move = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK | _MOUSEEVENTF_MOVE_NOCOALESCE
    arr = (_INPUT * 2)(
        _make_mouse_input(ax, ay, abs_move),
        _make_mouse_input(ax, ay, abs_move | down_flag),
    )
    return _USER32.SendInput(2, arr, ctypes.sizeof(_INPUT)) == 2


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
    sent = _USER32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))
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
