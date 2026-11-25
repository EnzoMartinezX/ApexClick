import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from pynput import keyboard, mouse
from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button
from pynput.mouse import Controller as MouseController

from engine import (
    MOUSE_BUTTON_NAMES,
    deserialize_key as _deserialize_key_pair,
    normalize_captured_key,
    same_key,
    serialize_key,
)
from win_utils import send_atomic_move


BUTTON_STR_TO_ENUM = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
}

BUTTON_ENUM_TO_STR = {v: k for k, v in BUTTON_STR_TO_ENUM.items()}


def deserialize_key(data: Dict[str, Any]) -> Optional[Union[Key, KeyCode]]:
    obj, _ = _deserialize_key_pair(data)
    return obj


def _key_identity(key: Union[Key, KeyCode]) -> Tuple[str, Any]:
    norm, _ = normalize_captured_key(key)
    if isinstance(norm, Key):
        return ("special", norm.name)
    if isinstance(norm, KeyCode):
        if norm.char:
            return ("char", norm.char.lower())
        return ("vk", norm.vk)
    return ("raw", str(norm))


@dataclass(frozen=True)
class MacroEvent:
    time_offset: float
    kind: str  # "key_down" | "key_up" | "mouse_down" | "mouse_up" | "mouse_move" | "mouse_scroll" | "pause"
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t": round(self.time_offset, 4),
            "kind": self.kind,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "MacroEvent":
        return cls(
            time_offset=float(raw.get("t", 0.0)),
            kind=str(raw["kind"]),
            data=dict(raw.get("data", {})),
        )

    def format_line(self, index: Optional[int] = None) -> str:
        prefix = f"#{index:03d}  " if index is not None else ""
        ts = f"{self.time_offset:6.2f}s"
        if self.kind == "key_down":
            return f"{prefix}{ts}  [TEC ↓]  {self.data.get('label', '?')}"
        if self.kind == "key_up":
            return f"{prefix}{ts}  [TEC ↑]  {self.data.get('label', '?')}"
        if self.kind == "mouse_down":
            btn = self.data.get("button", "left")
            lbl = MOUSE_BUTTON_NAMES.get(BUTTON_STR_TO_ENUM.get(btn, Button.left), btn)
            return f"{prefix}{ts}  [CLK ↓]  Mouse {lbl} ({self.data.get('x', 0)}, {self.data.get('y', 0)})"
        if self.kind == "mouse_up":
            btn = self.data.get("button", "left")
            lbl = MOUSE_BUTTON_NAMES.get(BUTTON_STR_TO_ENUM.get(btn, Button.left), btn)
            return f"{prefix}{ts}  [CLK ↑]  Mouse {lbl} ({self.data.get('x', 0)}, {self.data.get('y', 0)})"
        if self.kind == "mouse_scroll":
            dy = self.data.get("dy", 0)
            arrow = "↑" if dy >= 0 else "↓"
            return f"{prefix}{ts}  [SCR {arrow}]  dy={dy} ({self.data.get('x', 0)}, {self.data.get('y', 0)})"
        if self.kind == "mouse_move":
            return f"{prefix}{ts}  [MOV →]  ({self.data.get('x', 0)}, {self.data.get('y', 0)})"
        if self.kind == "pause":
            sec = float(self.data.get("seconds", 0.0))
            return f"{prefix}{ts}  [PAUSA]  Esperar {sec:.2f} s"
        return f"{prefix}{ts}  [{self.kind}]"


# =============================================================================
# Visual Macro Editor Helpers
# =============================================================================
def delete_event_at(events: Sequence[MacroEvent], index: int) -> List[MacroEvent]:
    """Delete step at `index` and rebase timeline cleanly so start stays at 0.0."""
    lst = list(events)
    if index < 0 or index >= len(lst):
        return lst
    lst.pop(index)
    if not lst:
        return []
    first_t = lst[0].time_offset
    if first_t > 0:
        lst = [MacroEvent(round(max(0.0, e.time_offset - first_t), 4), e.kind, dict(e.data)) for e in lst]
    return lst


def insert_pause_after(events: Sequence[MacroEvent], index: int, pause_sec: float) -> List[MacroEvent]:
    """Insert a manual pause after `index` and shift subsequent events forward."""
    sec = max(0.05, round(float(pause_sec), 3))
    lst = list(events)
    if not lst:
        return [MacroEvent(0.0, "pause", {"seconds": sec})]
    idx = max(0, min(index, len(lst) - 1))
    pause_t = lst[idx].time_offset
    out: List[MacroEvent] = list(lst[: idx + 1])
    out.append(MacroEvent(round(pause_t, 4), "pause", {"seconds": sec}))
    for ev in lst[idx + 1 :]:
        out.append(MacroEvent(round(ev.time_offset + sec, 4), ev.kind, dict(ev.data)))
    return out


def update_step_delay(events: Sequence[MacroEvent], index: int, new_gap_sec: float) -> List[MacroEvent]:
    """Change wait time before step `index` to `new_gap_sec`, shifting subsequent steps."""
    lst = list(events)
    if index <= 0 or index >= len(lst):
        return lst
    gap = max(0.0, round(float(new_gap_sec), 4))
    prev_t = lst[index - 1].time_offset
    old_t = lst[index].time_offset
    new_t = prev_t + gap
    delta = new_t - old_t
    out: List[MacroEvent] = list(lst[:index])
    for ev in lst[index:]:
        out.append(MacroEvent(round(max(0.0, ev.time_offset + delta), 4), ev.kind, dict(ev.data)))
    return out


def summarize_events(events: Sequence[MacroEvent]) -> Dict[str, Any]:
    key_events = sum(1 for e in events if e.kind in ("key_down", "key_up"))
    click_events = sum(1 for e in events if e.kind in ("mouse_down", "mouse_up", "mouse_scroll"))
    move_events = sum(1 for e in events if e.kind == "mouse_move")
    duration = 0.0
    if events:
        last = events[-1]
        extra = float(last.data.get("seconds", 0.0)) if last.kind == "pause" else 0.0
        duration = last.time_offset + extra
    return {
        "total": len(events),
        "keys": key_events,
        "clicks": click_events,
        "moves": move_events,
        "duration": duration,
    }


def save_macro_json(path: str, events: Sequence[MacroEvent]) -> None:
    payload = {
        "version": 1,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "events": [e.to_dict() for e in events],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def load_macro_json(path: str) -> List[MacroEvent]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    raw_events = payload.get("events", [])
    events = [MacroEvent.from_dict(item) for item in raw_events]
    events.sort(key=lambda e: e.time_offset)
    return events


class MacroRecorder:
    """Records keyboard and/or mouse events with dead-time trimming & key-repeat dedup."""

    MOVE_MIN_INTERVAL_SEC = 0.016
    MOVE_MIN_DIST_PX = 4

    def __init__(self, clock: Callable[[], float] = time.perf_counter):
        self._clock = clock
        self._lock = threading.Lock()
        self._recording = False
        self._events: List[MacroEvent] = []
        self._first_ts: Optional[float] = None
        self._start_ts: float = 0.0

        self._record_keyboard = True
        self._record_mouse_clicks = True
        self._record_mouse_moves = False
        self._excluded_keys: List[Union[Key, KeyCode]] = []

        self._held_keys: Dict[Tuple[str, Any], Dict[str, Any]] = {}
        self._held_buttons: Dict[str, Tuple[int, int]] = {}
        self._last_move_ts = 0.0
        self._last_move_pos: Optional[Tuple[int, int]] = None

        self._kb_listener: Optional[keyboard.Listener] = None
        self._mouse_listener: Optional[mouse.Listener] = None

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._recording

    def start(
        self,
        record_keyboard: bool = True,
        record_mouse_clicks: bool = True,
        record_mouse_moves: bool = False,
        excluded_keys: Optional[Sequence[Union[Key, KeyCode]]] = None,
        attach_listeners: bool = True,
    ) -> bool:
        if not (record_keyboard or record_mouse_clicks or record_mouse_moves):
            return False

        with self._lock:
            if self._recording:
                return False
            self._recording = True
            self._events = []
            self._first_ts = None
            self._start_ts = self._clock()
            self._record_keyboard = record_keyboard
            self._record_mouse_clicks = record_mouse_clicks
            self._record_mouse_moves = record_mouse_moves
            self._excluded_keys = list(excluded_keys or [])
            self._held_keys.clear()
            self._held_buttons.clear()
            self._last_move_ts = 0.0
            self._last_move_pos = None

        if attach_listeners:
            if record_keyboard:
                self._kb_listener = keyboard.Listener(
                    on_press=self.handle_key_press,
                    on_release=self.handle_key_release,
                )
                self._kb_listener.daemon = True
                self._kb_listener.start()

            if record_mouse_clicks or record_mouse_moves:
                self._mouse_listener = mouse.Listener(
                    on_move=self.handle_mouse_move if record_mouse_moves else None,
                    on_click=self.handle_mouse_click if record_mouse_clicks else None,
                    on_scroll=self.handle_mouse_scroll if record_mouse_clicks else None,
                )
                self._mouse_listener.daemon = True
                self._mouse_listener.start()

        return True

    def stop(self) -> List[MacroEvent]:
        kb_l = self._kb_listener
        m_l = self._mouse_listener
        self._kb_listener = None
        self._mouse_listener = None

        if kb_l is not None:
            try:
                kb_l.stop()
            except Exception:
                pass
        if m_l is not None:
            try:
                m_l.stop()
            except Exception:
                pass

        with self._lock:
            if not self._recording:
                return list(self._events)
            self._recording = False

            now = self._clock()
            if self._first_ts is not None:
                offset = max(0.0, now - self._first_ts)
                for _ident, kdata in list(self._held_keys.items()):
                    self._events.append(MacroEvent(offset, "key_up", dict(kdata)))
                for btn_name, (bx, by) in list(self._held_buttons.items()):
                    self._events.append(
                        MacroEvent(offset, "mouse_up", {"button": btn_name, "x": bx, "y": by})
                    )
            self._held_keys.clear()
            self._held_buttons.clear()
            return list(self._events)

    def get_live_stats(self) -> Tuple[bool, int, float]:
        with self._lock:
            elapsed = (self._clock() - self._start_ts) if self._recording else 0.0
            return self._recording, len(self._events), max(0.0, elapsed)

    def _is_excluded(self, key: Union[Key, KeyCode]) -> bool:
        return any(same_key(key, ex) for ex in self._excluded_keys)

    def _append_unlocked(self, kind: str, data: Dict[str, Any], now: float) -> None:
        if self._first_ts is None:
            self._first_ts = now
        offset = max(0.0, now - self._first_ts)
        self._events.append(MacroEvent(offset, kind, data))

    def handle_key_press(self, key: Union[Key, KeyCode]) -> None:
        now = self._clock()
        with self._lock:
            if not self._recording or not self._record_keyboard:
                return
            if self._is_excluded(key):
                return
            ident = _key_identity(key)
            if ident in self._held_keys:
                return
            kdata = serialize_key(key)
            self._held_keys[ident] = kdata
            self._append_unlocked("key_down", kdata, now)

    def handle_key_release(self, key: Union[Key, KeyCode]) -> None:
        now = self._clock()
        with self._lock:
            if not self._recording or not self._record_keyboard:
                return
            if self._is_excluded(key):
                return
            ident = _key_identity(key)
            kdata = self._held_keys.pop(ident, None)
            if kdata is None:
                return
            self._append_unlocked("key_up", kdata, now)

    def handle_mouse_click(self, x: int, y: int, button: Button, pressed: bool) -> None:
        now = self._clock()
        btn_name = BUTTON_ENUM_TO_STR.get(button)
        if btn_name is None:
            return
        with self._lock:
            if not self._recording or not self._record_mouse_clicks:
                return
            ix, iy = int(x), int(y)
            if pressed:
                self._held_buttons[btn_name] = (ix, iy)
                self._append_unlocked("mouse_down", {"button": btn_name, "x": ix, "y": iy}, now)
            else:
                if btn_name not in self._held_buttons:
                    return
                self._held_buttons.pop(btn_name, None)
                self._append_unlocked("mouse_up", {"button": btn_name, "x": ix, "y": iy}, now)

    def handle_mouse_move(self, x: int, y: int) -> None:
        now = self._clock()
        with self._lock:
            if not self._recording or not self._record_mouse_moves:
                return
            ix, iy = int(x), int(y)
            if self._last_move_pos is not None:
                dx = abs(ix - self._last_move_pos[0])
                dy = abs(iy - self._last_move_pos[1])
                if (now - self._last_move_ts) < self.MOVE_MIN_INTERVAL_SEC and max(dx, dy) < self.MOVE_MIN_DIST_PX:
                    return
            self._last_move_ts = now
            self._last_move_pos = (ix, iy)
            self._append_unlocked("mouse_move", {"x": ix, "y": iy}, now)

    def handle_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        now = self._clock()
        with self._lock:
            if not self._recording or not self._record_mouse_clicks:
                return
            self._append_unlocked(
                "mouse_scroll",
                {"x": int(x), "y": int(y), "dx": int(dx), "dy": int(dy)},
                now,
            )


@dataclass(frozen=True)
class MacroPlayConfig:
    speed: float = 1.0
    repeat_count: int = 1
    infinite_loop: bool = False
    loop_delay: float = 0.5


@dataclass(frozen=True)
class MacroSnapshot:
    running: bool
    phase: str
    current_loop: int
    completed_loops: int
    total_loops: int
    infinite: bool
    elapsed: float
    total_duration: float
    event_index: int
    total_events: int


class MacroPlayer:
    """Plays recorded MacroEvents cleanly with guaranteed key & button release on stop."""

    def __init__(
        self,
        mouse_controller: Optional[MouseController] = None,
        keyboard_controller: Optional[KeyboardController] = None,
        clock: Callable[[], float] = time.perf_counter,
        on_finished: Optional[Callable[[], None]] = None,
    ):
        self._using_default_mouse = mouse_controller is None
        self._mouse = mouse_controller or MouseController()
        self._keyboard = keyboard_controller or KeyboardController()
        self._clock = clock
        self._on_finished = on_finished

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._running = False
        self._phase = "LISTO"
        self._current_loop = 0
        self._completed_loops = 0
        self._total_loops = 1
        self._infinite = False
        self._loop_start = 0.0
        self._total_duration = 0.0
        self._event_index = 0
        self._total_events = 0

        self._active_keys: Dict[Tuple[str, Any], Union[Key, KeyCode]] = {}
        self._active_buttons: Set[Button] = set()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, events: Sequence[MacroEvent], config: MacroPlayConfig) -> bool:
        if not events:
            return False
        speed = max(0.1, min(10.0, config.speed))
        summary = summarize_events(events)
        total_dur = summary["duration"] / speed
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._phase = "REPRODUCIENDO"
            self._current_loop = 1
            self._completed_loops = 0
            self._total_loops = max(1, config.repeat_count)
            self._infinite = config.infinite_loop
            self._loop_start = self._clock()
            self._total_duration = total_dur
            self._event_index = 0
            self._total_events = len(events)
            self._active_keys.clear()
            self._active_buttons.clear()
            self._stop_event.clear()

        copied_events = list(events)
        self._thread = threading.Thread(
            target=self._run,
            args=(copied_events, config, speed),
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self, timeout: float = 0.8) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        with self._lock:
            self._release_all_unlocked()
            self._running = False
            self._phase = "LISTO"

    def get_snapshot(self) -> MacroSnapshot:
        with self._lock:
            elapsed = max(0.0, self._clock() - self._loop_start) if self._running else 0.0
            if self._total_duration > 0.0:
                elapsed = min(elapsed, self._total_duration)
            return MacroSnapshot(
                running=self._running,
                phase=self._phase,
                current_loop=self._current_loop,
                completed_loops=self._completed_loops,
                total_loops=self._total_loops,
                infinite=self._infinite,
                elapsed=elapsed,
                total_duration=self._total_duration,
                event_index=self._event_index,
                total_events=self._total_events,
            )

    def _release_all_unlocked(self) -> None:
        for key_obj in list(self._active_keys.values()):
            try:
                self._keyboard.release(key_obj)
            except Exception:
                pass
        self._active_keys.clear()

        for btn in list(self._active_buttons):
            try:
                self._mouse.release(btn)
            except Exception:
                pass
        self._active_buttons.clear()

    def _wait_until(self, deadline: float) -> bool:
        while not self._stop_event.is_set():
            rem = deadline - self._clock()
            if rem <= 0:
                return False
            self._stop_event.wait(min(0.015, rem))
        return True

    def _dispatch_event(self, ev: MacroEvent) -> None:
        if ev.kind == "pause":
            return
        if ev.kind == "key_down":
            key_obj = deserialize_key(ev.data)
            if key_obj is not None:
                ident = _key_identity(key_obj)
                with self._lock:
                    self._keyboard.press(key_obj)
                    self._active_keys[ident] = key_obj
        elif ev.kind == "key_up":
            key_obj = deserialize_key(ev.data)
            if key_obj is not None:
                ident = _key_identity(key_obj)
                with self._lock:
                    self._keyboard.release(key_obj)
                    self._active_keys.pop(ident, None)
        elif ev.kind == "mouse_move":
            self._mouse.position = (int(ev.data["x"]), int(ev.data["y"]))
        elif ev.kind == "mouse_down":
            btn = BUTTON_STR_TO_ENUM.get(ev.data.get("button", "left"), Button.left)
            self._mouse.position = (int(ev.data["x"]), int(ev.data["y"]))
            with self._lock:
                self._mouse.press(btn)
                self._active_buttons.add(btn)
        elif ev.kind == "mouse_up":
            btn = BUTTON_STR_TO_ENUM.get(ev.data.get("button", "left"), Button.left)
            self._mouse.position = (int(ev.data["x"]), int(ev.data["y"]))
            with self._lock:
                self._mouse.release(btn)
                self._active_buttons.discard(btn)
        elif ev.kind == "mouse_scroll":
            self._mouse.position = (int(ev.data["x"]), int(ev.data["y"]))
            self._mouse.scroll(int(ev.data.get("dx", 0)), int(ev.data.get("dy", 0)))

    def _run(self, events: List[MacroEvent], config: MacroPlayConfig, speed: float) -> None:
        try:
            loop_num = 0
            while not self._stop_event.is_set():
                loop_num += 1
                if not config.infinite_loop and loop_num > max(1, config.repeat_count):
                    break

                loop_start = self._clock()
                with self._lock:
                    self._current_loop = loop_num
                    self._phase = "REPRODUCIENDO"
                    self._loop_start = loop_start
                    self._event_index = 0

                prev_kind: Optional[str] = None
                for idx, ev in enumerate(events):
                    target_time = loop_start + (ev.time_offset / speed)
                    # When playing clicks without recorded mouse_move, pre-position via
                    # SendInput ~25ms ahead so Windows 11 taskbar & hover UIs register hover
                    # before mouse_down without shifting click timing!
                    if (
                        self._using_default_mouse
                        and ev.kind == "mouse_down"
                        and prev_kind != "mouse_move"
                    ):
                        pre_time = target_time - 0.025
                        if pre_time > self._clock():
                            if self._wait_until(pre_time):
                                return
                        send_atomic_move(int(ev.data["x"]), int(ev.data["y"]))
                        min_hover_until = max(target_time, self._clock() + 0.015)
                        if self._wait_until(min_hover_until):
                            return
                    else:
                        if self._wait_until(target_time):
                            return

                    self._dispatch_event(ev)
                    prev_kind = ev.kind
                    with self._lock:
                        self._event_index = idx + 1
                    # If the last event is an explicit pause, honor its duration too
                    if ev.kind == "pause" and idx == len(events) - 1:
                        extra = float(ev.data.get("seconds", 0.0)) / speed
                        if extra > 0 and self._wait_until(self._clock() + extra):
                            return

                with self._lock:
                    self._release_all_unlocked()
                    self._completed_loops = loop_num

                has_more = config.infinite_loop or loop_num < max(1, config.repeat_count)
                if has_more and config.loop_delay > 0:
                    pause_start = self._clock()
                    with self._lock:
                        self._phase = "PAUSA ENTRE CICLOS"
                        self._loop_start = pause_start
                    if self._wait_until(pause_start + config.loop_delay):
                        return
        finally:
            with self._lock:
                self._release_all_unlocked()
                self._running = False
                self._phase = "LISTO"
            if self._on_finished is not None:
                try:
                    self._on_finished()
                except Exception:
                    pass
