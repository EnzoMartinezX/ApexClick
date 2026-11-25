import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button
from pynput.mouse import Controller as MouseController

from win_utils import (
    get_cursor_pos_win32,
    get_foreground_window_title,
    is_in_failsafe_corner,
    send_atomic_press_at,
    send_atomic_release_at,
    send_atomic_zones_click,
)


SPECIAL_KEY_LABELS = {
    Key.space: "ESPACIO",
    Key.enter: "ENTER",
    Key.tab: "TAB",
    Key.backspace: "BORRAR",
    Key.esc: "ESC",
    Key.shift: "SHIFT",
    Key.shift_l: "SHIFT",
    Key.shift_r: "SHIFT DER",
    Key.ctrl: "CTRL",
    Key.ctrl_l: "CTRL",
    Key.ctrl_r: "CTRL DER",
    Key.alt: "ALT",
    Key.alt_l: "ALT",
    Key.alt_r: "ALT GR",
    Key.alt_gr: "ALT GR",
    Key.caps_lock: "BLOQ MAYUS",
    Key.up: "FLECHA ↑",
    Key.down: "FLECHA ↓",
    Key.left: "FLECHA ←",
    Key.right: "FLECHA →",
    Key.delete: "SUPR",
    Key.insert: "INS",
    Key.home: "INICIO",
    Key.end: "FIN",
    Key.page_up: "RE PAG",
    Key.page_down: "AV PAG",
}

MODIFIER_KEYS = {
    Key.shift, Key.shift_l, Key.shift_r,
    Key.ctrl, Key.ctrl_l, Key.ctrl_r,
    Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr,
}

MOUSE_BUTTON_NAMES = {
    Button.left: "Izq",
    Button.right: "Der",
    Button.middle: "Med",
}

MOUSE_STR_TO_BTN = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
}
MOUSE_BTN_TO_STR = {v: k for k, v in MOUSE_STR_TO_BTN.items()}


def resource_path(relative_path: str) -> str:
    """Resolve bundled asset paths for both dev and PyInstaller --onefile builds."""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, relative_path)


def parts_to_seconds(minutes: int, seconds: int, millis: int) -> float:
    total = max(0, minutes) * 60.0 + max(0, seconds) + max(0, millis) / 1000.0
    return round(total, 4)


def seconds_to_parts(total_seconds: float) -> Tuple[int, int, int]:
    total_ms = max(0, int(round(total_seconds * 1000)))
    minutes, rem_ms = divmod(total_ms, 60_000)
    seconds, millis = divmod(rem_ms, 1000)
    return minutes, seconds, millis


def format_timer(seconds: float) -> str:
    """Format duration as MM:SS.cc (or H:MM:SS.cc if >= 1 hour)."""
    clamped = max(0.0, seconds)
    total_centis = int(clamped * 100)
    centis = total_centis % 100
    total_secs = total_centis // 100
    secs = total_secs % 60
    total_mins = total_secs // 60
    mins = total_mins % 60
    hours = total_mins // 60
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}.{centis:02d}"
    return f"{mins:02d}:{secs:02d}.{centis:02d}"


def format_short_total(seconds: float) -> str:
    if seconds < 1.0:
        return f"{int(round(seconds * 1000))} ms"
    if seconds < 60.0:
        return f"{seconds:.2f} s"
    mins = int(seconds // 60)
    rem = seconds - mins * 60
    return f"{mins}m {rem:.1f}s"


def normalize_captured_key(key: Union[Key, KeyCode]) -> Tuple[Union[Key, KeyCode], str]:
    """Normalize a pynput key event into a controllable key object and display label."""
    if isinstance(key, Key):
        if key in SPECIAL_KEY_LABELS:
            return key, SPECIAL_KEY_LABELS[key]
        name = getattr(key, "name", str(key))
        return key, name.upper()

    char = getattr(key, "char", None)
    vk = getattr(key, "vk", None)

    if char and len(char) == 1 and char.isprintable() and not char.isspace():
        return KeyCode.from_char(char.lower()), char.upper()

    if vk is not None:
        if 65 <= vk <= 90:
            ch = chr(vk)
            return KeyCode.from_char(ch.lower()), ch
        if 48 <= vk <= 57:
            ch = chr(vk)
            return KeyCode.from_char(ch), ch
        if 96 <= vk <= 105:
            digit = vk - 96
            return KeyCode.from_vk(vk), f"NUM {digit}"
        return KeyCode.from_vk(vk), f"TECLA {vk}"

    return key, str(key).upper()


def serialize_key(key: Union[Key, KeyCode]) -> Dict[str, Any]:
    key_obj, label = normalize_captured_key(key)
    if isinstance(key_obj, Key):
        return {"type": "special", "name": key_obj.name, "label": label}
    if isinstance(key_obj, KeyCode):
        if key_obj.char:
            return {"type": "char", "value": key_obj.char, "label": label}
        if key_obj.vk is not None:
            return {"type": "vk", "value": int(key_obj.vk), "label": label}
    return {"type": "char", "value": str(key_obj), "label": label}


def deserialize_key(data: Dict[str, Any]) -> Tuple[Optional[Union[Key, KeyCode]], str]:
    if not isinstance(data, dict):
        return None, "?"
    ktype = data.get("type")
    label = str(data.get("label", "?"))
    if ktype == "special":
        obj = getattr(Key, str(data.get("name", "")), None)
        if obj is not None:
            _, norm_lbl = normalize_captured_key(obj)
            return obj, label or norm_lbl
        return None, label
    if ktype == "char":
        val = str(data.get("value", ""))
        if val:
            return KeyCode.from_char(val), label or val.upper()
    if ktype == "vk":
        vk = data.get("value")
        if vk is not None:
            return KeyCode.from_vk(int(vk)), label or f"TECLA {vk}"
    return None, label


def format_combo_label(keys: Sequence[Union[Key, KeyCode]]) -> str:
    if not keys:
        return "ESPACIO"
    return " + ".join(normalize_captured_key(k)[1] for k in keys)


def same_key(k1: Union[Key, KeyCode, None], k2: Union[Key, KeyCode, None]) -> bool:
    if k1 is None or k2 is None:
        return False
    if k1 == k2:
        return True
    if isinstance(k1, KeyCode) and isinstance(k2, KeyCode):
        c1 = (k1.char or "").lower()
        c2 = (k2.char or "").lower()
        if c1 and c2:
            return c1 == c2
        if k1.vk is not None and k2.vk is not None:
            return k1.vk == k2.vk
    return False


@dataclass(frozen=True)
class ClickerConfig:
    input_type: str = "mouse"                          # "mouse" | "keyboard"
    action_mode: str = "hold"                          # "click" | "hold"
    mouse_button: Button = Button.left
    click_count: int = 1                               # 1 (Simple) | 2 (Doble) | 3 (Triple)
    keyboard_key: Union[Key, KeyCode] = Key.space      # Backwards-compatible single key
    keyboard_keys: Optional[Tuple[Union[Key, KeyCode], ...]] = None  # Combo support
    hold_seconds: float = 1.5
    interval_seconds: float = 1.0
    infinite_hold: bool = False
    cursor_mode: str = "real"                          # "real" | "both" | "target_only"
    target_pos: Optional[Tuple[int, int]] = None       # Backwards-compatible single zone
    target_zones: Tuple[Tuple[int, int], ...] = ()     # Multi-zone sequence!
    stop_limit_mode: str = "none"                      # "none" | "cycles" | "minutes"
    stop_max_cycles: int = 100
    stop_max_minutes: float = 10.0
    countdown_seconds: float = 0.0
    target_window_title: str = ""                      # Empty = all windows
    failsafe_enabled: bool = False

    def resolved_keys(self) -> Tuple[Union[Key, KeyCode], ...]:
        if self.keyboard_keys:
            return tuple(self.keyboard_keys)
        return (self.keyboard_key,)

    def resolved_zones(self) -> Tuple[Tuple[int, int], ...]:
        if self.target_zones:
            return tuple(self.target_zones)
        if self.target_pos is not None:
            return (self.target_pos,)
        return ()


@dataclass(frozen=True)
class EngineSnapshot:
    running: bool
    phase: str
    phase_elapsed: float
    phase_total: float
    cycle_count: int
    is_pressed: bool
    infinite: bool
    total_elapsed: float = 0.0
    active_zone_index: int = 0
    total_zones: int = 0


class ClickerEngine:
    """Thread-safe Click / Hold engine with multi-zones, key combos, limits, window filter, and failsafe."""

    MIN_INTERVAL_SEC = 0.01
    MIN_HOLD_SEC = 0.02
    MIN_WAIT_GAP_SEC = 0.02
    KEY_TAP_SEC = 0.02
    MULTI_CLICK_GAP_SEC = 0.015

    def __init__(
        self,
        mouse_controller: Optional[MouseController] = None,
        keyboard_controller: Optional[KeyboardController] = None,
        clock: Callable[[], float] = time.perf_counter,
        target_pos_provider: Optional[Callable[[], Optional[Tuple[int, int]]]] = None,
        target_zones_provider: Optional[Callable[[], Tuple[Tuple[int, int], ...]]] = None,
        foreground_title_provider: Optional[Callable[[], str]] = None,
        on_finished: Optional[Callable[[str], None]] = None,
        use_atomic_win32: Optional[bool] = None,
    ):
        self._using_default_mouse = mouse_controller is None
        self._mouse = mouse_controller or MouseController()
        self._keyboard = keyboard_controller or KeyboardController()
        self._clock = clock
        self._target_pos_provider = target_pos_provider
        self._target_zones_provider = target_zones_provider
        self._foreground_title_provider = foreground_title_provider or get_foreground_window_title
        self._on_finished = on_finished
        # Use atomic Win32 SendInput by default on real mouse; mock controllers use controller API
        self._use_atomic_win32 = self._using_default_mouse if use_atomic_win32 is None else use_atomic_win32

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._running = False
        self._phase = "DETENIDO"
        self._phase_start = 0.0
        self._phase_total = 0.0
        self._run_start = 0.0
        self._cycle_count = 0
        self._is_pressed = False
        self._pressed_keys: List[Union[Key, KeyCode]] = []
        self._infinite = False
        self._active_zone_index = 0
        self._total_zones = 0
        self._active_config: Optional[ClickerConfig] = None
        self._stop_reason = "manual"

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, config: ClickerConfig) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._active_config = config
            self._cycle_count = 0
            self._is_pressed = False
            self._pressed_keys.clear()
            self._infinite = config.action_mode == "hold" and config.infinite_hold
            self._phase = "INICIANDO"
            now = self._clock()
            self._phase_start = now
            self._run_start = now
            self._phase_total = 0.0
            self._active_zone_index = 0
            self._total_zones = len(config.resolved_zones())
            self._stop_reason = "manual"
            self._stop_event.clear()

        self._thread = threading.Thread(target=self._run_loop, args=(config,), daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 0.8) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        with self._lock:
            cfg = self._active_config
            if self._is_pressed and cfg is not None:
                self._release_unlocked(cfg)
            self._running = False
            self._phase = "DETENIDO"
            self._phase_total = 0.0
            self._infinite = False

    def get_snapshot(self) -> EngineSnapshot:
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._phase_start) if self._running else 0.0
            if self._phase_total > 0.0:
                elapsed = min(elapsed, self._phase_total)
            total_el = max(0.0, now - self._run_start) if self._running else 0.0
            return EngineSnapshot(
                running=self._running,
                phase=self._phase,
                phase_elapsed=elapsed,
                phase_total=self._phase_total,
                cycle_count=self._cycle_count,
                is_pressed=self._is_pressed,
                infinite=self._infinite,
                total_elapsed=total_el,
                active_zone_index=self._active_zone_index,
                total_zones=self._total_zones,
            )

    def _resolve_zones(self, config: ClickerConfig) -> Tuple[Tuple[int, int], ...]:
        if self._target_zones_provider is not None:
            zones = self._target_zones_provider()
            if zones:
                return tuple(zones)
        if self._target_pos_provider is not None:
            pos = self._target_pos_provider()
            if pos is not None:
                return (pos,)
        return config.resolved_zones()

    def _get_cursor_pos(self) -> Optional[Tuple[int, int]]:
        if self._use_atomic_win32:
            pos = get_cursor_pos_win32()
            if pos is not None:
                return pos
        try:
            pos = self._mouse.position
            if pos is not None:
                return (int(pos[0]), int(pos[1]))
        except Exception:
            pass
        return None

    def _set_cursor_pos(self, pos: Optional[Tuple[int, int]]) -> None:
        if pos is None:
            return
        try:
            self._mouse.position = (int(pos[0]), int(pos[1]))
        except Exception:
            pass

    def _press(self, config: ClickerConfig) -> None:
        with self._lock:
            if self._is_pressed:
                return
            if config.input_type == "mouse":
                self._mouse.press(config.mouse_button)
                self._is_pressed = True
            else:
                keys = config.resolved_keys()
                for k in keys:
                    self._keyboard.press(k)
                    self._pressed_keys.append(k)
                self._is_pressed = True

    def _release_unlocked(self, config: ClickerConfig) -> None:
        if not self._is_pressed and not self._pressed_keys:
            return
        try:
            if config.input_type == "mouse":
                if self._is_pressed:
                    self._mouse.release(config.mouse_button)
            else:
                # Release in reverse order (main key first, then modifiers)
                for k in reversed(self._pressed_keys):
                    try:
                        self._keyboard.release(k)
                    except Exception:
                        pass
                self._pressed_keys.clear()
        finally:
            self._is_pressed = False

    def _release(self, config: ClickerConfig) -> None:
        with self._lock:
            self._release_unlocked(config)

    def _set_phase(self, phase: str, total: float = 0.0, zone_idx: int = 0) -> None:
        with self._lock:
            self._phase = phase
            self._phase_start = self._clock()
            self._phase_total = total
            self._active_zone_index = zone_idx

    def _increment_cycles(self) -> int:
        with self._lock:
            self._cycle_count += 1
            return self._cycle_count

    def _check_failsafe(self, config: ClickerConfig) -> bool:
        if not config.failsafe_enabled:
            return False
        pos = self._get_cursor_pos()
        if is_in_failsafe_corner(pos):
            self._stop_reason = "failsafe"
            self._stop_event.set()
            return True
        return False

    def _check_limits(self, config: ClickerConfig) -> bool:
        if config.stop_limit_mode == "cycles" and config.stop_max_cycles > 0:
            with self._lock:
                if self._cycle_count >= config.stop_max_cycles:
                    self._stop_reason = "cycles"
                    self._stop_event.set()
                    return True
        elif config.stop_limit_mode == "minutes" and config.stop_max_minutes > 0:
            if (self._clock() - self._run_start) >= (config.stop_max_minutes * 60.0):
                self._stop_reason = "time"
                self._stop_event.set()
                return True
        return False

    def _is_target_window_active(self, config: ClickerConfig) -> bool:
        target = config.target_window_title.strip()
        if not target:
            return True
        current = self._foreground_title_provider() or ""
        return target.lower() in current.lower()

    def _wait_for_target_window(self, config: ClickerConfig) -> bool:
        """Pause cleanly while the configured window is not focused. Returns True if stopped."""
        if self._is_target_window_active(config):
            return False
        self._release(config)
        self._set_phase("PAUSADO (VENTANA)", 0.0)
        while not self._stop_event.is_set():
            if self._check_failsafe(config) or self._check_limits(config):
                return True
            if self._is_target_window_active(config):
                return False
            self._stop_event.wait(0.12)
        return True

    def _wait_interruptible(
        self,
        duration: float,
        config: ClickerConfig,
        check_window: bool = False,
        check_failsafe: bool = False,
    ) -> Tuple[bool, bool]:
        """Wait up to duration. Returns (stopped, window_lost)."""
        deadline = self._clock() + duration
        last_win_check = self._clock()
        while not self._stop_event.is_set():
            if check_failsafe and self._check_failsafe(config):
                return True, False
            if self._check_limits(config):
                return True, False
            now = self._clock()
            if check_window and (now - last_win_check) >= 0.12:
                last_win_check = now
                if not self._is_target_window_active(config):
                    return False, True
            remaining = deadline - now
            if remaining <= 0:
                return False, False
            self._stop_event.wait(min(0.015, remaining))
        return True, False

    def _do_multi_click(self, config: ClickerConfig) -> None:
        count = max(1, min(3, config.click_count))
        for i in range(count):
            if self._stop_event.is_set():
                return
            self._mouse.click(config.mouse_button)
            if i < count - 1:
                self._stop_event.wait(self.MULTI_CLICK_GAP_SEC)

    def _perform_mouse_click_step(self, config: ClickerConfig) -> None:
        mode = config.cursor_mode
        zones = self._resolve_zones(config)

        if mode == "real" or not zones:
            self._do_multi_click(config)
            return

        if mode == "both":
            self._do_multi_click(config)
            if self._stop_event.is_set():
                return

        count = max(1, min(3, config.click_count))
        btn_name = MOUSE_BTN_TO_STR.get(config.mouse_button, "left")

        with self._lock:
            self._total_zones = len(zones)

        # Click ALL zones in ONE atomic batch per wave (never sampling GetCursorPos between
        # consecutive zones, eliminating the race that could snap the real mouse to a zone!)
        for wave in range(count):
            if self._stop_event.is_set():
                return
            orig = self._get_cursor_pos()
            if self._use_atomic_win32:
                send_atomic_zones_click(zones, btn_name, restore_pos=orig)
            else:
                for idx, pt in enumerate(zones, start=1):
                    if self._stop_event.is_set():
                        return
                    with self._lock:
                        self._active_zone_index = idx
                    self._set_cursor_pos(pt)
                    self._mouse.click(config.mouse_button)
                self._set_cursor_pos(orig)

            if wave < count - 1:
                if self._stop_event.wait(self.MULTI_CLICK_GAP_SEC):
                    return

    def _run_countdown(self, config: ClickerConfig) -> bool:
        if config.countdown_seconds <= 0:
            return False
        total = float(config.countdown_seconds)
        self._set_phase("CUENTA REGRESIVA", total)
        stopped, _ = self._wait_interruptible(total, config, check_failsafe=False)
        if not stopped:
            # Reset run_start after countdown so time limit measures actual execution time
            with self._lock:
                self._run_start = self._clock()
        return stopped

    def _run_loop(self, config: ClickerConfig) -> None:
        reason = "manual"
        try:
            if self._run_countdown(config):
                return

            if config.action_mode == "click":
                self._run_click_mode(config)
            elif config.infinite_hold:
                self._run_infinite_hold_mode(config)
            else:
                self._run_timed_hold_mode(config)
        finally:
            self._release(config)
            with self._lock:
                reason = self._stop_reason
                self._running = False
                self._phase = "DETENIDO"
                self._phase_total = 0.0
                self._infinite = False
            if reason != "manual" and self._on_finished is not None:
                try:
                    self._on_finished(reason)
                except Exception:
                    pass

    def _run_click_mode(self, config: ClickerConfig) -> None:
        interval = max(self.MIN_INTERVAL_SEC, config.interval_seconds)
        while not self._stop_event.is_set():
            if self._check_failsafe(config) or self._check_limits(config):
                break
            if self._wait_for_target_window(config):
                break

            self._set_phase("CLICKEANDO", interval)
            if config.input_type == "mouse":
                self._perform_mouse_click_step(config)
            else:
                self._press(config)
                tap_time = min(self.KEY_TAP_SEC, interval / 2.0)
                stopped, _ = self._wait_interruptible(tap_time, config)
                self._release(config)
                if stopped:
                    break

            self._increment_cycles()
            if self._check_limits(config) or self._stop_event.is_set():
                break

            self._set_phase("ESPERANDO", interval)
            stopped, _ = self._wait_interruptible(
                interval, config, check_window=bool(config.target_window_title), check_failsafe=True
            )
            if stopped:
                break

    def _run_infinite_hold_mode(self, config: ClickerConfig) -> None:
        while not self._stop_event.is_set():
            if self._check_failsafe(config) or self._check_limits(config):
                break
            if self._wait_for_target_window(config):
                break

            orig: Optional[Tuple[int, int]] = None
            moved = False
            if config.input_type == "mouse" and config.cursor_mode in ("target_only", "both"):
                zones = self._resolve_zones(config)
                if zones:
                    orig = self._get_cursor_pos()
                    if config.cursor_mode == "both":
                        self._mouse.click(config.mouse_button)
                    self._set_cursor_pos(zones[0])
                    moved = True

            try:
                self._press(config)
                self._increment_cycles()
                label = "MANTENIENDO ZONA (∞)" if moved else "MANTENIENDO (∞)"
                self._set_phase(label, 0.0, zone_idx=1 if moved else 0)
                while not self._stop_event.is_set():
                    if not moved and self._check_failsafe(config):
                        return
                    if self._check_limits(config):
                        return
                    if config.target_window_title and not self._is_target_window_active(config):
                        break
                    self._stop_event.wait(0.03)
            finally:
                self._release(config)
                if moved:
                    self._set_cursor_pos(orig)

    def _run_timed_hold_mode(self, config: ClickerConfig) -> None:
        hold_duration = max(self.MIN_HOLD_SEC, config.hold_seconds)
        wait_duration = max(self.MIN_WAIT_GAP_SEC, config.interval_seconds)

        while not self._stop_event.is_set():
            if self._check_failsafe(config) or self._check_limits(config):
                break
            if self._wait_for_target_window(config):
                break

            zones = self._resolve_zones(config) if config.input_type == "mouse" else ()
            use_zones = config.input_type == "mouse" and bool(zones) and config.cursor_mode != "real"
            win_filter = bool(config.target_window_title)

            if not use_zones:
                self._press(config)
                self._set_phase("MANTENIENDO", hold_duration)
                stopped, win_lost = self._wait_interruptible(
                    hold_duration, config, check_window=win_filter, check_failsafe=True
                )
                self._release(config)
                if stopped:
                    break
                if win_lost:
                    continue
            else:
                if config.cursor_mode == "both":
                    self._press(config)
                    self._set_phase("MANTENIENDO (REAL)", hold_duration)
                    stopped, win_lost = self._wait_interruptible(
                        hold_duration, config, check_window=win_filter, check_failsafe=True
                    )
                    self._release(config)
                    if stopped:
                        break
                    if win_lost:
                        continue

                # Read real cursor ONCE before holding zones so Zone #2 can never capture Zone #1
                orig = self._get_cursor_pos()
                aborted = False
                for idx, pt in enumerate(zones, start=1):
                    if self._stop_event.is_set():
                        aborted = True
                        break
                    tag = f"MANTENIENDO ZONA #{idx}" if len(zones) > 1 else "MANTENIENDO (ZONA)"
                    btn_name = MOUSE_BTN_TO_STR.get(config.mouse_button, "left")
                    try:
                        if self._use_atomic_win32:
                            send_atomic_press_at(pt[0], pt[1], btn_name)
                            with self._lock:
                                self._is_pressed = True
                        else:
                            self._set_cursor_pos(pt)
                            self._press(config)
                        self._set_phase(tag, hold_duration, zone_idx=idx)
                        stopped, win_lost = self._wait_interruptible(
                            hold_duration, config, check_window=win_filter, check_failsafe=False
                        )
                    finally:
                        if self._use_atomic_win32:
                            with self._lock:
                                if self._is_pressed:
                                    send_atomic_release_at(pt[0], pt[1], btn_name, restore_pos=orig)
                                    self._is_pressed = False
                        else:
                            self._release(config)
                            self._set_cursor_pos(orig)
                    if stopped or win_lost:
                        aborted = True
                        break
                if aborted:
                    if self._stop_event.is_set():
                        break
                    continue

            self._increment_cycles()
            if self._check_limits(config) or self._stop_event.is_set():
                break

            self._set_phase("ESPERANDO", wait_duration)
            stopped, _ = self._wait_interruptible(
                wait_duration, config, check_window=win_filter, check_failsafe=True
            )
            if stopped:
                break
