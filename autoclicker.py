import os
import time
import tkinter as tk
from tkinter import colorchooser, filedialog
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import customtkinter as ctk
from pynput import keyboard
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button

from config_store import (
    DEFAULT_STATE,
    get_macros_dir,
    list_saved_macros,
    load_config_file,
    save_config_file,
)
from engine import (
    MODIFIER_KEYS,
    MOUSE_BTN_TO_STR,
    MOUSE_STR_TO_BTN,
    ClickerConfig,
    ClickerEngine,
    deserialize_key,
    format_combo_label,
    format_short_total,
    format_timer,
    normalize_captured_key,
    parts_to_seconds,
    resource_path,
    same_key,
    seconds_to_parts,
    serialize_key,
)
from i18n import tr, translate_engine_phase
from macro_engine import (
    MacroEvent,
    MacroPlayConfig,
    MacroPlayer,
    MacroRecorder,
    delete_event_at,
    insert_pause_after,
    load_macro_json,
    save_macro_json,
    summarize_events,
    update_step_delay,
)
from target_overlay import (
    MiniHudWindow,
    TargetMarkerWindow,
    resolve_reticle_colors,
)
from win_utils import list_open_windows, play_tone


# =============================================================================
# Theme: Obsidian Black & Electric Neon Blue (+ Holographic Green for Zones)
# =============================================================================
COLORS = {
    "bg": "#04060B",
    "sidebar": "#060A14",
    "card": "#0A101C",
    "card_dim": "#070B14",
    "card_inner": "#050912",
    "border": "#132E52",
    "border_dim": "#0E2038",
    "border_blue": "#0080FF",
    "border_glow": "#00F0FF",
    "neon_cyan": "#00F0FF",
    "neon_blue": "#007BFF",
    "neon_blue_hover": "#00A2FF",
    "neon_dim": "#0B203B",
    "holo_green": "#39FF14",
    "holo_green_dim": "#0A260D",
    "text_primary": "#EAF6FF",
    "text_secondary": "#7CA8D1",
    "text_muted": "#476582",
    "input_bg": "#04070E",
    "danger": "#FF2E63",
    "danger_bg": "#1C0813",
    "danger_hover": "#310E21",
    "danger_dim_border": "#42162A",
    "accent_sky": "#38BDF8",
    "warn_amber": "#FBBF24",
}


def _canonical_modifier(key: Union[Key, KeyCode]) -> Optional[Key]:
    if key in (Key.ctrl, Key.ctrl_l, Key.ctrl_r):
        return Key.ctrl_l
    if key in (Key.shift, Key.shift_l, Key.shift_r):
        return Key.shift
    if key in (Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr):
        return Key.alt_l
    return None


# =============================================================================
# Compact Digital Chronometer Card Widget
# =============================================================================
class ChronometerCard(ctk.CTkFrame):
    """Neon digital timer (MIN : SEG . MS) with hold-to-measure stopwatch."""

    def __init__(
        self,
        master,
        title: str,
        accent_color: str,
        default_seconds: float,
        subtitle: str,
        show_infinite_toggle: bool = False,
        on_infinite_change: Optional[Callable[[bool], None]] = None,
    ):
        super().__init__(
            master,
            fg_color=COLORS["card"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["border"],
        )

        self._accent = accent_color
        self._enabled = True
        self._measuring = False
        self._measure_start = 0.0
        self._measure_after_id: Optional[str] = None
        self._on_infinite_change = on_infinite_change
        self._measure_label = "⏱ Medir"
        self._infinite_badge_label = "∞ CONTINUO"

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))

        self.label_title = ctk.CTkLabel(
            header,
            text=title,
            font=("Segoe UI", 11, "bold"),
            text_color=accent_color,
        )
        self.label_title.pack(side="left")

        self.badge_total = ctk.CTkLabel(
            header,
            text="",
            font=("Consolas", 10, "bold"),
            text_color=COLORS["neon_cyan"],
            fg_color=COLORS["neon_dim"],
            corner_radius=6,
            padx=6,
            pady=1,
        )
        self.badge_total.pack(side="right")

        inputs_row = ctk.CTkFrame(self, fg_color="transparent")
        inputs_row.pack(fill="x", padx=12, pady=(2, 2))

        mins, secs, ms = seconds_to_parts(default_seconds)

        self.entry_min, self._lbl_min = self._create_time_field(inputs_row, f"{mins:02d}", "MIN", 52)
        self._sep1 = self._create_sep(inputs_row, ":")
        self.entry_sec, self._lbl_sec = self._create_time_field(inputs_row, f"{secs:02d}", "SEG", 52)
        self._sep2 = self._create_sep(inputs_row, ".")
        self.entry_ms, self._lbl_ms = self._create_time_field(inputs_row, f"{ms:03d}", "MS", 58)

        controls_row = ctk.CTkFrame(self, fg_color="transparent")
        controls_row.pack(fill="x", padx=12, pady=(3, 4))

        self.btn_measure = ctk.CTkButton(
            controls_row,
            text=self._measure_label,
            width=88,
            height=28,
            corner_radius=7,
            font=("Segoe UI", 11, "bold"),
            fg_color=COLORS["neon_dim"],
            hover_color="#12365E",
            border_width=1,
            border_color=COLORS["border_blue"],
            text_color=COLORS["neon_cyan"],
            command=lambda: None,
        )
        self.btn_measure.pack(side="left")
        self.btn_measure.bind("<ButtonPress-1>", self._start_measure)
        self.btn_measure.bind("<ButtonRelease-1>", self._stop_measure)

        self.infinite_var = ctk.BooleanVar(value=False)
        self.switch_infinite: Optional[ctk.CTkSwitch] = None
        if show_infinite_toggle:
            self.switch_infinite = ctk.CTkSwitch(
                controls_row,
                text="Sin soltar (∞)",
                variable=self.infinite_var,
                command=self._handle_infinite_toggle,
                font=("Segoe UI", 10, "bold"),
                text_color=COLORS["text_secondary"],
                fg_color="#12263F",
                progress_color=COLORS["neon_blue"],
                button_color="#6EA4D4",
                button_hover_color=COLORS["neon_cyan"],
                switch_width=32,
                switch_height=16,
            )
            self.switch_infinite.pack(side="right")

        self.label_sub = ctk.CTkLabel(
            self,
            text=subtitle,
            font=("Segoe UI", 10),
            text_color=COLORS["text_muted"],
            anchor="w",
        )
        self.label_sub.pack(fill="x", padx=12, pady=(0, 9))

        self._refresh_total_badge()

    def apply_language(
        self,
        title: str,
        unit_min: str,
        unit_sec: str,
        unit_ms: str,
        measure_label: str,
        infinite_label: str = "",
        infinite_badge: str = "∞ CONTINUO",
    ) -> None:
        self._measure_label = measure_label
        self._infinite_badge_label = infinite_badge
        self.label_title.configure(text=title)
        self._lbl_min.configure(text=unit_min)
        self._lbl_sec.configure(text=unit_sec)
        self._lbl_ms.configure(text=unit_ms)
        if not self._measuring:
            self.btn_measure.configure(text=measure_label)
        if self.switch_infinite is not None and infinite_label:
            self.switch_infinite.configure(text=infinite_label)
        self._refresh_total_badge()

    def _create_time_field(self, parent, initial: str, unit: str, width: int) -> Tuple[ctk.CTkEntry, ctk.CTkLabel]:
        col = ctk.CTkFrame(parent, fg_color="transparent")
        col.pack(side="left", expand=True)

        entry = ctk.CTkEntry(
            col,
            width=width,
            height=32,
            justify="center",
            font=("Consolas", 14, "bold"),
            fg_color=COLORS["input_bg"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            corner_radius=7,
        )
        entry.insert(0, initial)
        entry.pack()
        entry.bind("<KeyRelease>", lambda _e: self._refresh_total_badge())
        entry.bind("<FocusOut>", lambda _e: self._normalize_inputs())

        lbl = ctk.CTkLabel(
            col,
            text=unit,
            font=("Segoe UI", 8, "bold"),
            text_color=COLORS["text_muted"],
        )
        lbl.pack(pady=(1, 0))
        return entry, lbl

    def _create_sep(self, parent, char: str) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(
            parent,
            text=char,
            font=("Consolas", 16, "bold"),
            text_color=COLORS["neon_blue"],
            width=10,
        )
        lbl.pack(side="left", pady=(0, 12))
        return lbl

    def _parse_int(self, entry: ctk.CTkEntry, max_val: int) -> int:
        raw = "".join(ch for ch in entry.get() if ch.isdigit())
        return min(max_val, int(raw)) if raw else 0

    def get_seconds(self) -> float:
        return parts_to_seconds(
            self._parse_int(self.entry_min, 999),
            self._parse_int(self.entry_sec, 9999),
            self._parse_int(self.entry_ms, 9999),
        )

    def set_seconds(self, total_seconds: float) -> None:
        m, s, ms = seconds_to_parts(total_seconds)
        was_disabled = not self._enabled
        if was_disabled:
            self._set_entries_state("normal")
        self.entry_min.delete(0, "end")
        self.entry_min.insert(0, f"{m:02d}")
        self.entry_sec.delete(0, "end")
        self.entry_sec.insert(0, f"{s:02d}")
        self.entry_ms.delete(0, "end")
        self.entry_ms.insert(0, f"{ms:03d}")
        if was_disabled:
            self._set_entries_state("disabled")
        self._refresh_total_badge()

    def _normalize_inputs(self) -> None:
        if self._enabled:
            self.set_seconds(self.get_seconds())

    def _refresh_total_badge(self) -> None:
        if self.infinite_var.get():
            self.badge_total.configure(text=self._infinite_badge_label)
        else:
            self.badge_total.configure(text=format_short_total(self.get_seconds()))

    def _handle_infinite_toggle(self) -> None:
        self._refresh_total_badge()
        if self._on_infinite_change:
            self._on_infinite_change(self.infinite_var.get())

    def _start_measure(self, _event) -> None:
        if not self._enabled or self._measuring:
            return
        self._measuring = True
        self._measure_start = time.perf_counter()
        self.btn_measure.configure(
            fg_color=COLORS["neon_blue"],
            border_color=COLORS["neon_cyan"],
            text_color="#FFFFFF",
            text="0.00 s",
        )
        self._tick_measure()

    def _tick_measure(self) -> None:
        if not self._measuring:
            return
        elapsed = time.perf_counter() - self._measure_start
        self.btn_measure.configure(text=f"{elapsed:.2f} s")
        self._measure_after_id = self.after(25, self._tick_measure)

    def _stop_measure(self, _event) -> None:
        if not self._measuring:
            return
        self._measuring = False
        if self._measure_after_id is not None:
            self.after_cancel(self._measure_after_id)
            self._measure_after_id = None
        elapsed = max(0.05, time.perf_counter() - self._measure_start)
        self.set_seconds(elapsed)
        self.btn_measure.configure(
            fg_color=COLORS["neon_dim"],
            border_color=COLORS["border_blue"],
            text_color=COLORS["neon_cyan"],
            text=self._measure_label,
        )

    def _set_entries_state(self, state: str) -> None:
        for entry in (self.entry_min, self.entry_sec, self.entry_ms):
            entry.configure(state=state)

    def set_subtitle(self, text: str) -> None:
        self.label_sub.configure(text=text)

    def set_inputs_enabled(self, enabled: bool, allow_infinite_switch: bool = True) -> None:
        self._enabled = enabled
        state = "normal" if enabled else "disabled"
        self._set_entries_state(state)
        self.btn_measure.configure(state=state)

        if self.switch_infinite is not None:
            self.switch_infinite.configure(
                state="normal" if allow_infinite_switch else "disabled",
                button_color=COLORS["neon_cyan"]
                if (allow_infinite_switch and self.infinite_var.get())
                else ("#6EA4D4" if allow_infinite_switch else COLORS["text_muted"]),
                text_color=COLORS["text_secondary"]
                if allow_infinite_switch
                else COLORS["text_muted"],
            )

        if enabled:
            self.configure(fg_color=COLORS["card"], border_color=COLORS["border"])
            self.label_title.configure(text_color=self._accent)
            self.badge_total.configure(text_color=COLORS["neon_cyan"], fg_color=COLORS["neon_dim"])
            self._sep1.configure(text_color=COLORS["neon_blue"])
            self._sep2.configure(text_color=COLORS["neon_blue"])
            self.btn_measure.configure(
                fg_color=COLORS["neon_dim"],
                border_color=COLORS["border_blue"],
                text_color=COLORS["neon_cyan"],
            )
            for entry in (self.entry_min, self.entry_sec, self.entry_ms):
                entry.configure(text_color=COLORS["text_primary"], border_color=COLORS["border"])
        else:
            self.configure(fg_color=COLORS["card_dim"], border_color=COLORS["border_dim"])
            self.label_title.configure(text_color=COLORS["text_muted"])
            self.badge_total.configure(text_color=COLORS["text_muted"], fg_color="#0A1320")
            self._sep1.configure(text_color=COLORS["text_muted"])
            self._sep2.configure(text_color=COLORS["text_muted"])
            self.btn_measure.configure(
                fg_color="#0A1320",
                border_color=COLORS["border_dim"],
                text_color=COLORS["text_muted"],
            )
            for entry in (self.entry_min, self.entry_sec, self.entry_ms):
                entry.configure(text_color=COLORS["text_muted"], border_color=COLORS["border_dim"])


# =============================================================================
# Main Application: ApexClick v2 (Spacious 5-Page Neon Dashboard, ES/EN)
# =============================================================================
class AutoclickerApp(ctk.CTk):
    NAV_KEYS = [
        ("clicker", "nav_clicker"),
        ("zones", "nav_zones"),
        ("macros", "nav_macros"),
        ("limits", "nav_limits"),
        ("profiles", "nav_profiles"),
    ]

    def __init__(self, config_path: Optional[str] = None):
        super().__init__()

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        # Keep hidden at alpha=0 while building widgets so startup has zero white flash
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            pass

        self.lang = "es"  # Default: Spanish
        self.title(self._t("window_title"))
        self.geometry("780x650")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])

        # Persistent Config & Profiles
        self._config_path = config_path
        self.config_data = load_config_file(self._config_path)

        # Target Zones (Multi-Point Holographic Reticles)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.zone_coords: List[Tuple[int, int]] = [(sw // 2, sh // 2)]
        self.zone_overlays: List[TargetMarkerWindow] = []
        self.zones_visible = False
        self.cursor_mode = "real"
        self.reticle_color = "green"
        self.reticle_show_numbers = True

        # Mini-HUD Window
        self.mini_hud: Optional[MiniHudWindow] = None

        # Core Engines (thread-safe zone snapshot reader — never calls Tk from worker thread!)
        self.engine = ClickerEngine(
            target_zones_provider=lambda: tuple(self.zone_coords),
            on_finished=lambda reason: self.after(0, lambda r=reason: self._on_clicker_auto_finished(r)),
        )
        self.macro_recorder = MacroRecorder()
        self.macro_player = MacroPlayer(
            on_finished=lambda: self.after(0, self._on_macro_playback_finished)
        )
        self.macro_events: List[MacroEvent] = []

        # Core State
        self.active_page = "clicker"
        self.input_type = "mouse"
        self.action_mode = "hold"
        self.mouse_button = Button.left
        self.click_count = 1
        self.target_keys: List[Union[Key, KeyCode]] = [Key.space]
        self.stop_limit_mode = "none"  # "none" | "cycles" | "minutes"

        # Global Hotkeys (all 3 customizable!)
        self.hotkey_clicker: Union[Key, KeyCode] = Key.f6
        self.hotkey_clicker_label = "F6"
        self.hotkey_macro_record: Union[Key, KeyCode] = Key.f7
        self.hotkey_macro_record_label = "F7"
        self.hotkey_macro_play: Union[Key, KeyCode] = Key.f8
        self.hotkey_macro_play_label = "F8"

        # Capture & Timers
        self.capturing_mode: Optional[str] = None
        self._held_modifiers: List[Key] = []
        self._held_hotkeys: set = set()
        self._pending_start_after: Optional[str] = None
        self._pending_macro_after: Optional[str] = None
        self._hud_after_id: Optional[str] = None

        # Build UI & Hydrate saved config
        self._build_ui()
        self._apply_state_dict(self.config_data.get("last_state", DEFAULT_STATE), show_overlays=False)
        self._refresh_profile_dropdown()
        self._refresh_macro_library()
        self._refresh_open_windows_list()
        self._apply_language()

        self.update_idletasks()
        self.update()
        self.after(35, self._reveal_main_window)
        self.after(250, self._apply_icon)

        # Single global keyboard listener (press + release for combo capture + hotkeys)
        self.listener = keyboard.Listener(
            on_press=self._on_global_key_press,
            on_release=self._on_global_key_release,
        )
        self.listener.daemon = True
        self.listener.start()

        self._schedule_hud_update()

    def _t(self, key: str, **kwargs) -> str:
        return tr(self.lang, key, **kwargs)

    def _apply_icon(self) -> None:
        ico_path = resource_path("neon_icon.ico")
        if os.path.exists(ico_path):
            try:
                self.iconbitmap(ico_path)
            except Exception:
                pass

    # =========================================================================
    # Root Layout: Neon Accent + Left Sidebar + Right Content Stage
    # =========================================================================
    def _build_ui(self) -> None:
        ctk.CTkFrame(self, height=3, corner_radius=0, fg_color=COLORS["neon_cyan"]).pack(fill="x")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)

        # --- Left Navigation Sidebar ---
        sidebar = ctk.CTkFrame(body, width=182, corner_radius=0, fg_color=COLORS["sidebar"])
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ctk.CTkFrame(body, width=1, corner_radius=0, fg_color=COLORS["border"]).pack(side="left", fill="y")

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=14, pady=(16, 12))

        b_row = ctk.CTkFrame(brand, fg_color="transparent")
        b_row.pack(anchor="w")
        ctk.CTkLabel(
            b_row, text="APEXCLICK", font=("Segoe UI", 17, "bold"), text_color=COLORS["neon_cyan"],
        ).pack(side="left")
        ctk.CTkLabel(
            b_row, text="v2", font=("Consolas", 10, "bold"), text_color="#FFFFFF",
            fg_color=COLORS["neon_blue"], corner_radius=5, padx=5, pady=1,
        ).pack(side="left", padx=(6, 0))
        self.lbl_brand_sub = ctk.CTkLabel(
            brand, text=self._t("brand_sub"), font=("Segoe UI", 9, "bold"), text_color=COLORS["text_muted"],
        )
        self.lbl_brand_sub.pack(anchor="w")

        # Persistent Live Status Card inside Sidebar
        self.sidebar_status_card = ctk.CTkFrame(
            sidebar, fg_color=COLORS["card_inner"], corner_radius=10, border_width=1, border_color=COLORS["border"],
        )
        self.sidebar_status_card.pack(fill="x", padx=12, pady=(0, 14))

        self.label_sb_status = ctk.CTkLabel(
            self.sidebar_status_card, text="●  DETENIDO", font=("Segoe UI", 10, "bold"),
            text_color=COLORS["text_muted"], anchor="w",
        )
        self.label_sb_status.pack(fill="x", padx=10, pady=(7, 0))

        self.label_sb_sub = ctk.CTkLabel(
            self.sidebar_status_card, text="Mouse · Mantener", font=("Consolas", 9),
            text_color=COLORS["text_secondary"], anchor="w",
        )
        self.label_sb_sub.pack(fill="x", padx=10, pady=(1, 7))

        # Navigation Buttons
        self.nav_buttons: Dict[str, ctk.CTkButton] = {}
        for page_id, t_key in self.NAV_KEYS:
            btn = ctk.CTkButton(
                sidebar,
                text=self._t(t_key),
                height=38,
                corner_radius=9,
                anchor="w",
                font=("Segoe UI", 12, "bold"),
                fg_color="transparent",
                hover_color=COLORS["neon_dim"],
                border_width=1,
                border_color=COLORS["sidebar"],
                text_color=COLORS["text_secondary"],
                command=lambda pid=page_id: self._select_page(pid),
            )
            btn.pack(fill="x", padx=10, pady=3)
            self.nav_buttons[page_id] = btn

        # Bottom Sidebar Quick Controls
        sb_bottom = ctk.CTkFrame(sidebar, fg_color="transparent")
        sb_bottom.pack(side="bottom", fill="x", padx=12, pady=14)

        self.topmost_var = ctk.BooleanVar(value=False)
        self.switch_topmost = ctk.CTkSwitch(
            sb_bottom,
            text=self._t("pin_window"),
            variable=self.topmost_var,
            command=self._toggle_topmost,
            font=("Segoe UI", 11, "bold"),
            text_color=COLORS["text_secondary"],
            fg_color="#102238",
            progress_color=COLORS["neon_blue"],
            button_color="#6EA4D4",
            button_hover_color=COLORS["neon_cyan"],
            switch_width=34,
            switch_height=17,
        )
        self.switch_topmost.pack(anchor="w", pady=(0, 10))

        self.btn_mini_hud = ctk.CTkButton(
            sb_bottom,
            text=self._t("mini_hud_btn"),
            height=32,
            corner_radius=8,
            font=("Segoe UI", 11, "bold"),
            fg_color=COLORS["card"],
            hover_color=COLORS["neon_dim"],
            border_width=1,
            border_color=COLORS["border_blue"],
            text_color=COLORS["neon_cyan"],
            command=self._open_mini_hud,
        )
        self.btn_mini_hud.pack(fill="x")

        # --- Right Stage ---
        self.stage = ctk.CTkFrame(body, fg_color="transparent")
        self.stage.pack(side="left", fill="both", expand=True, padx=18, pady=14)

        self.pages: Dict[str, ctk.CTkFrame] = {}
        for page_id, _ in self.NAV_KEYS:
            self.pages[page_id] = ctk.CTkFrame(self.stage, fg_color="transparent")

        self._build_page_clicker(self.pages["clicker"])
        self._build_page_zones(self.pages["zones"])
        self._build_page_macros(self.pages["macros"])
        self._build_page_limits(self.pages["limits"])
        self._build_page_profiles(self.pages["profiles"])

        self._select_page("clicker")

    def _select_page(self, page_id: str) -> None:
        self.active_page = page_id
        for pid, frame in self.pages.items():
            if pid == page_id:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

        for pid, btn in self.nav_buttons.items():
            if pid == page_id:
                btn.configure(fg_color=COLORS["neon_blue"], border_color=COLORS["neon_cyan"], text_color="#FFFFFF")
            else:
                btn.configure(fg_color="transparent", border_color=COLORS["sidebar"], text_color=COLORS["text_secondary"])

    # =========================================================================
    # Page 1: Autoclicker
    # =========================================================================
    def _build_page_clicker(self, parent: ctk.CTkFrame) -> None:
        self.hud_card = ctk.CTkFrame(
            parent, fg_color=COLORS["card_inner"], corner_radius=14, border_width=2, border_color=COLORS["border"],
        )
        self.hud_card.pack(fill="x", pady=(0, 12))

        hud_top = ctk.CTkFrame(self.hud_card, fg_color="transparent")
        hud_top.pack(fill="x", padx=16, pady=(10, 2))

        self.label_phase = ctk.CTkLabel(hud_top, text="●  DETENIDO", font=("Segoe UI", 12, "bold"), text_color=COLORS["text_muted"])
        self.label_phase.pack(side="left")

        self.label_cycles = ctk.CTkLabel(hud_top, text="Ciclos: 0", font=("Consolas", 12, "bold"), text_color=COLORS["text_secondary"])
        self.label_cycles.pack(side="right")

        hud_mid = ctk.CTkFrame(self.hud_card, fg_color="transparent")
        hud_mid.pack(fill="x", padx=16, pady=(0, 6))

        self.label_clock = ctk.CTkLabel(hud_mid, text="00:00.00", font=("Consolas", 32, "bold"), text_color=COLORS["text_muted"])
        self.label_clock.pack(side="left")

        self.label_clock_target = ctk.CTkLabel(hud_mid, text="/ 00:01.50", font=("Consolas", 14, "bold"), text_color=COLORS["text_muted"])
        self.label_clock_target.pack(side="left", padx=(8, 0), pady=(10, 0))

        self.label_hud_badges = ctk.CTkLabel(hud_mid, text="", font=("Consolas", 10, "bold"), text_color=COLORS["holo_green"])
        self.label_hud_badges.pack(side="right", pady=(10, 0))

        self.progress_bar = ctk.CTkProgressBar(
            self.hud_card, height=8, corner_radius=4, fg_color="#0A192C",
            progress_color="#0A192C", border_width=1, border_color="#123456",
        )
        self.progress_bar.pack(fill="x", padx=16, pady=(0, 12))
        self.progress_bar.set(0.0)

        self.config_card = ctk.CTkFrame(
            parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"],
        )
        self.config_card.pack(fill="x", pady=(0, 12))

        seg_row = ctk.CTkFrame(self.config_card, fg_color="transparent")
        seg_row.pack(fill="x", padx=14, pady=(10, 8))
        seg_row.grid_columnconfigure(0, weight=1)
        seg_row.grid_columnconfigure(1, weight=1)

        c_in = ctk.CTkFrame(seg_row, fg_color="transparent")
        c_in.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.lbl_control = ctk.CTkLabel(c_in, text="CONTROLAR", font=("Segoe UI", 10, "bold"), text_color=COLORS["text_secondary"])
        self.lbl_control.pack(anchor="w", pady=(0, 4))
        self.seg_input = ctk.CTkSegmentedButton(
            c_in, values=["Mouse", "Teclado"], command=self._on_input_type_change,
            height=32, corner_radius=8, font=("Segoe UI", 12, "bold"),
            fg_color=COLORS["input_bg"], selected_color=COLORS["neon_blue"],
            selected_hover_color=COLORS["neon_blue_hover"], unselected_color=COLORS["input_bg"],
            unselected_hover_color=COLORS["neon_dim"], text_color=COLORS["text_primary"],
        )
        self.seg_input.set("Mouse")
        self.seg_input.pack(fill="x")

        c_mode = ctk.CTkFrame(seg_row, fg_color="transparent")
        c_mode.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.lbl_action_mode = ctk.CTkLabel(c_mode, text="MODO DE ACCIÓN", font=("Segoe UI", 10, "bold"), text_color=COLORS["text_secondary"])
        self.lbl_action_mode.pack(anchor="w", pady=(0, 4))
        self.seg_mode = ctk.CTkSegmentedButton(
            c_mode, values=["Mantener", "Click"], command=self._on_action_mode_change,
            height=32, corner_radius=8, font=("Segoe UI", 12, "bold"),
            fg_color=COLORS["input_bg"], selected_color=COLORS["neon_blue"],
            selected_hover_color=COLORS["neon_blue_hover"], unselected_color=COLORS["input_bg"],
            unselected_hover_color=COLORS["neon_dim"], text_color=COLORS["text_primary"],
        )
        self.seg_mode.set("Mantener")
        self.seg_mode.pack(fill="x")

        self.target_holder = ctk.CTkFrame(self.config_card, fg_color="transparent")
        self.target_holder.pack(fill="x", padx=14, pady=(0, 12))

        self.frame_mouse_target = ctk.CTkFrame(self.target_holder, fg_color="transparent")
        self.frame_mouse_target.grid_columnconfigure(0, weight=3)
        self.frame_mouse_target.grid_columnconfigure(1, weight=2)

        self.seg_mouse_btn = ctk.CTkSegmentedButton(
            self.frame_mouse_target, values=["Izquierdo", "Derecho", "Medio"],
            command=self._on_mouse_button_change, height=30, corner_radius=8,
            font=("Segoe UI", 11, "bold"), fg_color=COLORS["input_bg"],
            selected_color="#0055CC", selected_hover_color=COLORS["neon_blue"],
            unselected_color=COLORS["input_bg"], unselected_hover_color=COLORS["neon_dim"],
            text_color=COLORS["text_primary"],
        )
        self.seg_mouse_btn.set("Izquierdo")
        self.seg_mouse_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.seg_click_count = ctk.CTkSegmentedButton(
            self.frame_mouse_target, values=["Simple", "Doble", "Triple"],
            command=self._on_click_count_change, height=30, corner_radius=8,
            font=("Segoe UI", 11, "bold"), fg_color=COLORS["input_bg"],
            selected_color="#0055CC", selected_hover_color=COLORS["neon_blue"],
            unselected_color=COLORS["input_bg"], unselected_hover_color=COLORS["neon_dim"],
            text_color=COLORS["text_primary"],
        )
        self.seg_click_count.set("Simple")
        self.seg_click_count.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.frame_key_target = ctk.CTkFrame(self.target_holder, fg_color="transparent")
        self.label_key_badge = ctk.CTkLabel(
            self.frame_key_target, text="[ ESPACIO ]", font=("Consolas", 13, "bold"),
            text_color=COLORS["neon_cyan"], fg_color=COLORS["input_bg"], corner_radius=8, height=30, padx=12,
        )
        self.label_key_badge.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_capture_key = ctk.CTkButton(
            self.frame_key_target, text="Tecla", width=84, height=30, corner_radius=8,
            font=("Segoe UI", 11, "bold"), fg_color=COLORS["neon_dim"], hover_color="#12365E",
            border_width=1, border_color=COLORS["border_blue"], text_color=COLORS["neon_cyan"],
            command=lambda: self._begin_capture("target_single"),
        )
        self.btn_capture_key.pack(side="left", padx=(0, 6))

        self.btn_capture_combo = ctk.CTkButton(
            self.frame_key_target, text="Combo (+)", width=96, height=30, corner_radius=8,
            font=("Segoe UI", 11, "bold"), fg_color=COLORS["neon_dim"], hover_color="#12365E",
            border_width=1, border_color=COLORS["neon_cyan"], text_color=COLORS["text_primary"],
            command=lambda: self._begin_capture("target_combo"),
        )
        self.btn_capture_combo.pack(side="left")

        chrono_row = ctk.CTkFrame(parent, fg_color="transparent")
        chrono_row.pack(fill="x", pady=(0, 14))
        chrono_row.grid_columnconfigure(0, weight=1, uniform="chrono")
        chrono_row.grid_columnconfigure(1, weight=1, uniform="chrono")

        self.card_hold = ChronometerCard(
            chrono_row, title="⏱ TIEMPO MANTENIDO", accent_color=COLORS["neon_cyan"],
            default_seconds=1.5, subtitle="", show_infinite_toggle=True,
            on_infinite_change=self._on_infinite_hold_change,
        )
        self.card_hold.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.card_interval = ChronometerCard(
            chrono_row, title="⏱ TURNO / INTERVALO", accent_color=COLORS["accent_sky"],
            default_seconds=1.0, subtitle="", show_infinite_toggle=False,
        )
        self.card_interval.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 10))
        btn_row.grid_columnconfigure(0, weight=3)
        btn_row.grid_columnconfigure(1, weight=2)

        self.btn_start = ctk.CTkButton(
            btn_row, text="", height=46, corner_radius=10, font=("Segoe UI", 14, "bold"),
            fg_color=COLORS["neon_blue"], hover_color=COLORS["neon_blue_hover"],
            border_width=2, border_color=COLORS["neon_cyan"], text_color="#FFFFFF",
            command=lambda: self.start_clicking(from_button=True),
        )
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_stop = ctk.CTkButton(
            btn_row, text="", height=46, corner_radius=10, font=("Segoe UI", 13, "bold"),
            fg_color=COLORS["danger_bg"], hover_color=COLORS["danger_hover"],
            border_width=2, border_color=COLORS["danger_dim_border"], text_color=COLORS["text_muted"],
            state="disabled", command=self.stop_clicking,
        )
        self.btn_stop.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.label_footer_info = ctk.CTkLabel(
            parent, text=self._t("footer_ready"), font=("Segoe UI", 11), text_color=COLORS["text_muted"], anchor="w",
        )
        self.label_footer_info.pack(fill="x")

    # =========================================================================
    # Page 2: Multi-Zone Second Mouse
    # =========================================================================
    def _build_page_zones(self, parent: ctk.CTkFrame) -> None:
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 10))
        self.lbl_zones_title = ctk.CTkLabel(hdr, text="", font=("Segoe UI", 16, "bold"), text_color=COLORS["holo_green"])
        self.lbl_zones_title.pack(anchor="w")
        self.lbl_zones_sub = ctk.CTkLabel(hdr, text="", font=("Segoe UI", 11), text_color=COLORS["text_secondary"])
        self.lbl_zones_sub.pack(anchor="w")

        mode_card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        mode_card.pack(fill="x", pady=(0, 10))

        self.lbl_cursor_mode = ctk.CTkLabel(mode_card, text="", font=("Segoe UI", 10, "bold"), text_color=COLORS["text_secondary"])
        self.lbl_cursor_mode.pack(anchor="w", padx=14, pady=(10, 5))

        self.seg_cursor_mode = ctk.CTkSegmentedButton(
            mode_card, values=["Mouse real", "Ambos (Real + Zonas)", "Solo zonas fijas"],
            command=self._on_cursor_mode_change, height=34, corner_radius=8,
            font=("Segoe UI", 11, "bold"), fg_color=COLORS["input_bg"],
            selected_color="#0066DD", selected_hover_color=COLORS["neon_blue"],
            unselected_color=COLORS["input_bg"], unselected_hover_color=COLORS["neon_dim"],
            text_color=COLORS["text_primary"],
        )
        self.seg_cursor_mode.set("Mouse real")
        self.seg_cursor_mode.pack(fill="x", padx=14, pady=(0, 6))

        self.label_zone_hint = ctk.CTkLabel(mode_card, text="", font=("Segoe UI", 11), text_color=COLORS["text_muted"], anchor="w")
        self.label_zone_hint.pack(fill="x", padx=14, pady=(0, 10))

        # Appearance Card
        style_card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        style_card.pack(fill="x", pady=(0, 10))

        st_hdr = ctk.CTkFrame(style_card, fg_color="transparent")
        st_hdr.pack(fill="x", padx=14, pady=(8, 6))

        self.lbl_reticle_color = ctk.CTkLabel(st_hdr, text="", font=("Segoe UI", 10, "bold"), text_color=COLORS["text_secondary"])
        self.lbl_reticle_color.pack(side="left")

        self.var_show_zone_nums = ctk.BooleanVar(value=True)
        self.switch_zone_nums = ctk.CTkSwitch(
            st_hdr, text="", variable=self.var_show_zone_nums, command=self._on_reticle_numbers_toggle,
            font=("Segoe UI", 10, "bold"), text_color=COLORS["text_secondary"],
            fg_color="#12263F", progress_color=COLORS["neon_blue"], button_color=COLORS["neon_cyan"],
            switch_width=32, switch_height=16,
        )
        self.switch_zone_nums.pack(side="right")

        color_row = ctk.CTkFrame(style_card, fg_color="transparent")
        color_row.pack(fill="x", padx=14, pady=(0, 10))
        for col_i in range(6):
            color_row.grid_columnconfigure(col_i, weight=1, uniform="clr")

        self.reticle_color_buttons: Dict[str, ctk.CTkButton] = {}
        color_defs = [
            ("green", "col_green", "#39FF14"),
            ("red", "col_red", "#FF2A4B"),
            ("cyan", "col_cyan", "#00F0FF"),
            ("pink", "col_pink", "#FF3ED6"),
            ("rainbow", "col_rainbow", "#FDE047"),
        ]
        for idx_c, (key, t_key, swatch) in enumerate(color_defs):
            btn = ctk.CTkButton(
                color_row, text=self._t(t_key), width=10, height=28, corner_radius=7,
                font=("Segoe UI", 10, "bold"), fg_color=COLORS["input_bg"], hover_color=COLORS["neon_dim"],
                border_width=1, border_color=COLORS["border"], text_color=swatch,
                command=lambda k=key: self._set_reticle_color(k),
            )
            btn.grid(row=0, column=idx_c, sticky="ew", padx=(0, 5))
            self.reticle_color_buttons[key] = btn

        self.btn_reticle_custom = ctk.CTkButton(
            color_row, text="", width=10, height=28, corner_radius=7,
            font=("Segoe UI", 10, "bold"), fg_color=COLORS["input_bg"], hover_color=COLORS["neon_dim"],
            border_width=1, border_color=COLORS["border"], text_color=COLORS["text_secondary"],
            command=self._pick_custom_reticle_color,
        )
        self.btn_reticle_custom.grid(row=0, column=5, sticky="ew")

        list_card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        list_card.pack(fill="both", expand=True, pady=(0, 8))

        tb = ctk.CTkFrame(list_card, fg_color="transparent")
        tb.pack(fill="x", padx=14, pady=(10, 8))

        self.label_zones_count = ctk.CTkLabel(tb, text="", font=("Segoe UI", 11, "bold"), text_color=COLORS["holo_green"])
        self.label_zones_count.pack(side="left")

        self.btn_zone_reset = ctk.CTkButton(
            tb, text="Reset", width=58, height=28, corner_radius=7, font=("Segoe UI", 10, "bold"),
            fg_color=COLORS["danger_bg"], hover_color=COLORS["danger_hover"],
            border_width=1, border_color=COLORS["danger_dim_border"], text_color="#FF7A9C",
            command=self._reset_zones,
        )
        self.btn_zone_reset.pack(side="right", padx=(6, 0))

        self.btn_toggle_zone = ctk.CTkButton(
            tb, text="", width=126, height=28, corner_radius=7, font=("Segoe UI", 10, "bold"),
            fg_color=COLORS["holo_green_dim"], hover_color="#124218",
            border_width=1, border_color=COLORS["holo_green"], text_color=COLORS["holo_green"],
            command=self._toggle_zone_overlays,
        )
        self.btn_toggle_zone.pack(side="right", padx=(6, 0))

        self.btn_add_zone = ctk.CTkButton(
            tb, text="", width=116, height=28, corner_radius=7, font=("Segoe UI", 10, "bold"),
            fg_color=COLORS["neon_blue"], hover_color=COLORS["neon_blue_hover"],
            border_width=1, border_color=COLORS["neon_cyan"], text_color="#FFFFFF",
            command=self._add_zone,
        )
        self.btn_add_zone.pack(side="right")

        self.zones_scroll = ctk.CTkScrollableFrame(
            list_card, fg_color=COLORS["input_bg"], corner_radius=9, border_width=1, border_color=COLORS["border_dim"],
        )
        self.zones_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self._zone_coord_labels: List[ctk.CTkLabel] = []

        self.lbl_zones_tip = ctk.CTkLabel(parent, text="", font=("Segoe UI", 10), text_color=COLORS["text_muted"], anchor="w")
        self.lbl_zones_tip.pack(fill="x")

    # =========================================================================
    # Page 3: Macros
    # =========================================================================
    def _build_page_macros(self, parent: ctk.CTkFrame) -> None:
        self.macro_hud_card = ctk.CTkFrame(
            parent, fg_color=COLORS["card_inner"], corner_radius=12, border_width=2, border_color=COLORS["border"],
        )
        self.macro_hud_card.pack(fill="x", pady=(0, 9))

        mh_row = ctk.CTkFrame(self.macro_hud_card, fg_color="transparent")
        mh_row.pack(fill="x", padx=14, pady=(8, 4))

        self.label_macro_phase = ctk.CTkLabel(mh_row, text="●  LISTO", font=("Segoe UI", 11, "bold"), text_color=COLORS["text_muted"])
        self.label_macro_phase.pack(side="left")

        self.label_macro_clock = ctk.CTkLabel(mh_row, text="00:00.00 / 00:00.00", font=("Consolas", 14, "bold"), text_color=COLORS["text_muted"])
        self.label_macro_clock.pack(side="left", padx=(14, 0))

        self.label_macro_counters = ctk.CTkLabel(mh_row, text="", font=("Consolas", 11, "bold"), text_color=COLORS["text_secondary"])
        self.label_macro_counters.pack(side="right")

        self.macro_progress = ctk.CTkProgressBar(
            self.macro_hud_card, height=6, corner_radius=3, fg_color="#0A192C",
            progress_color="#0A192C", border_width=1, border_color="#123456",
        )
        self.macro_progress.pack(fill="x", padx=14, pady=(0, 9))
        self.macro_progress.set(0.0)

        rec_card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        rec_card.pack(fill="x", pady=(0, 9))

        r1 = ctk.CTkFrame(rec_card, fg_color="transparent")
        r1.pack(fill="x", padx=12, pady=(8, 6))

        self.var_rec_keyboard = ctk.BooleanVar(value=True)
        self.var_rec_mouse_clicks = ctk.BooleanVar(value=True)
        self.var_rec_mouse_moves = ctk.BooleanVar(value=False)

        self.chk_rec_kb = ctk.CTkCheckBox(
            r1, text="", variable=self.var_rec_keyboard, font=("Segoe UI", 11, "bold"),
            text_color=COLORS["text_primary"], fg_color=COLORS["neon_blue"],
            hover_color=COLORS["neon_blue_hover"], border_color=COLORS["border_blue"],
            checkbox_width=17, checkbox_height=17, width=88,
        )
        self.chk_rec_kb.pack(side="left", padx=(0, 8))

        self.chk_rec_mouse = ctk.CTkCheckBox(
            r1, text="", variable=self.var_rec_mouse_clicks, font=("Segoe UI", 11, "bold"),
            text_color=COLORS["text_primary"], fg_color=COLORS["neon_blue"],
            hover_color=COLORS["neon_blue_hover"], border_color=COLORS["border_blue"],
            checkbox_width=17, checkbox_height=17, width=120,
        )
        self.chk_rec_mouse.pack(side="left", padx=(0, 8))

        self.chk_rec_moves = ctk.CTkCheckBox(
            r1, text="", variable=self.var_rec_mouse_moves, font=("Segoe UI", 11, "bold"),
            text_color=COLORS["text_secondary"], fg_color=COLORS["neon_blue"],
            hover_color=COLORS["neon_blue_hover"], border_color=COLORS["border_blue"],
            checkbox_width=17, checkbox_height=17, width=108,
        )
        self.chk_rec_moves.pack(side="left")

        self.btn_macro_record = ctk.CTkButton(
            r1, text="", width=142, height=30, corner_radius=8, font=("Segoe UI", 11, "bold"),
            fg_color=COLORS["neon_dim"], hover_color="#12365E", border_width=1,
            border_color=COLORS["neon_cyan"], text_color=COLORS["neon_cyan"],
            command=lambda: self.toggle_macro_recording(from_button=True),
        )
        self.btn_macro_record.pack(side="right")

        r2 = ctk.CTkFrame(rec_card, fg_color="transparent")
        r2.pack(fill="x", padx=12, pady=(0, 9))

        self.lbl_library = ctk.CTkLabel(r2, text="", font=("Segoe UI", 10, "bold"), text_color=COLORS["text_secondary"])
        self.lbl_library.pack(side="left", padx=(0, 6))
        self.opt_macro_lib = ctk.CTkOptionMenu(
            r2, values=["(Sin macros guardadas)"], command=self._on_macro_lib_select,
            width=190, height=26, corner_radius=7, font=("Segoe UI", 10),
            fg_color=COLORS["input_bg"], button_color=COLORS["neon_dim"],
            button_hover_color=COLORS["neon_blue"], dropdown_fg_color=COLORS["card"],
            text_color=COLORS["text_primary"],
        )
        self.opt_macro_lib.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_macro_save = ctk.CTkButton(
            r2, text="", width=66, height=26, corner_radius=7, font=("Segoe UI", 10, "bold"),
            fg_color=COLORS["neon_dim"], hover_color="#12365E", border_width=1,
            border_color=COLORS["border_blue"], text_color=COLORS["neon_cyan"], command=self._save_macro_dialog,
        )
        self.btn_macro_save.pack(side="left", padx=(0, 4))

        self.btn_macro_load = ctk.CTkButton(
            r2, text="", width=58, height=26, corner_radius=7, font=("Segoe UI", 10, "bold"),
            fg_color=COLORS["neon_dim"], hover_color="#12365E", border_width=1,
            border_color=COLORS["border"], text_color=COLORS["text_primary"], command=self._load_macro_dialog,
        )
        self.btn_macro_load.pack(side="left", padx=(0, 4))

        self.btn_macro_clear = ctk.CTkButton(
            r2, text="", width=58, height=26, corner_radius=7, font=("Segoe UI", 10, "bold"),
            fg_color=COLORS["danger_bg"], hover_color=COLORS["danger_hover"], border_width=1,
            border_color=COLORS["danger_dim_border"], text_color="#FF7A9C", command=self._clear_macro,
        )
        self.btn_macro_clear.pack(side="left")

        play_card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        play_card.pack(fill="x", pady=(0, 9))

        pr = ctk.CTkFrame(play_card, fg_color="transparent")
        pr.pack(fill="x", padx=12, pady=8)

        self.lbl_rep = ctk.CTkLabel(pr, text="Rep:", font=("Segoe UI", 11, "bold"), text_color=COLORS["text_secondary"])
        self.lbl_rep.pack(side="left")
        self.entry_macro_repeats = ctk.CTkEntry(
            pr, width=44, height=28, justify="center", font=("Consolas", 12, "bold"),
            fg_color=COLORS["input_bg"], border_width=1, border_color=COLORS["border"],
            text_color=COLORS["text_primary"], corner_radius=7,
        )
        self.entry_macro_repeats.insert(0, "1")
        self.entry_macro_repeats.pack(side="left", padx=(4, 8))

        self.var_macro_infinite = ctk.BooleanVar(value=False)
        self.switch_macro_inf = ctk.CTkSwitch(
            pr, text="∞", variable=self.var_macro_infinite, command=self._sync_macro_ui,
            font=("Segoe UI", 11, "bold"), text_color=COLORS["text_secondary"],
            fg_color="#12263F", progress_color=COLORS["neon_blue"], button_color="#6EA4D4",
            switch_width=32, switch_height=16, width=54,
        )
        self.switch_macro_inf.pack(side="left", padx=(0, 8))

        self.lbl_pause_s = ctk.CTkLabel(pr, text="", font=("Segoe UI", 10), text_color=COLORS["text_secondary"])
        self.lbl_pause_s.pack(side="left")
        self.entry_macro_delay = ctk.CTkEntry(
            pr, width=48, height=28, justify="center", font=("Consolas", 11, "bold"),
            fg_color=COLORS["input_bg"], border_width=1, border_color=COLORS["border"],
            text_color=COLORS["text_primary"], corner_radius=7,
        )
        self.entry_macro_delay.insert(0, "0.5")
        self.entry_macro_delay.pack(side="left", padx=(4, 10))

        self.seg_macro_speed = ctk.CTkSegmentedButton(
            pr, values=["0.5x", "1x", "1.5x", "2x"], height=28, corner_radius=7,
            font=("Consolas", 10, "bold"), fg_color=COLORS["input_bg"],
            selected_color=COLORS["neon_blue"], selected_hover_color=COLORS["neon_blue_hover"],
            unselected_color=COLORS["input_bg"], unselected_hover_color=COLORS["neon_dim"],
            text_color=COLORS["text_primary"],
        )
        self.seg_macro_speed.set("1x")
        self.seg_macro_speed.pack(side="right", fill="x", expand=True)

        seq_card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        seq_card.pack(fill="both", expand=True, pady=(0, 10))

        ed_hdr = ctk.CTkFrame(seq_card, fg_color="transparent")
        ed_hdr.pack(fill="x", padx=12, pady=(8, 5))

        self.lbl_step_editor = ctk.CTkLabel(ed_hdr, text="", font=("Segoe UI", 10, "bold"), text_color=COLORS["accent_sky"])
        self.lbl_step_editor.pack(side="left")

        self.badge_macro_summary = ctk.CTkLabel(
            ed_hdr, text="", font=("Consolas", 10, "bold"),
            text_color=COLORS["neon_cyan"], fg_color=COLORS["neon_dim"], corner_radius=6, padx=6, pady=1,
        )
        self.badge_macro_summary.pack(side="left", padx=(8, 0))

        self.btn_step_delete = ctk.CTkButton(
            ed_hdr, text="", width=78, height=24, corner_radius=6, font=("Segoe UI", 10, "bold"),
            fg_color=COLORS["danger_bg"], hover_color=COLORS["danger_hover"], border_width=1,
            border_color=COLORS["danger_dim_border"], text_color="#FF7A9C", command=self._delete_selected_macro_step,
        )
        self.btn_step_delete.pack(side="right", padx=(4, 0))

        self.btn_step_delay = ctk.CTkButton(
            ed_hdr, text="", width=92, height=24, corner_radius=6, font=("Segoe UI", 10, "bold"),
            fg_color=COLORS["neon_dim"], hover_color="#12365E", border_width=1,
            border_color=COLORS["border_blue"], text_color=COLORS["text_primary"], command=self._edit_selected_step_delay,
        )
        self.btn_step_delay.pack(side="right", padx=(4, 0))

        self.btn_step_pause = ctk.CTkButton(
            ed_hdr, text="", width=62, height=24, corner_radius=6, font=("Segoe UI", 10, "bold"),
            fg_color=COLORS["neon_dim"], hover_color="#12365E", border_width=1,
            border_color=COLORS["neon_cyan"], text_color=COLORS["neon_cyan"], command=self._insert_pause_at_selected,
        )
        self.btn_step_pause.pack(side="right", padx=(4, 0))

        self.entry_step_sec = ctk.CTkEntry(
            ed_hdr, width=48, height=24, justify="center", font=("Consolas", 10, "bold"),
            fg_color=COLORS["input_bg"], border_width=1, border_color=COLORS["border"],
            text_color=COLORS["neon_cyan"], corner_radius=6,
        )
        self.entry_step_sec.insert(0, "0.5")
        self.entry_step_sec.pack(side="right")

        lb_wrap = ctk.CTkFrame(seq_card, fg_color=COLORS["input_bg"], corner_radius=8, border_width=1, border_color=COLORS["border_dim"])
        lb_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        self.listbox_macro = tk.Listbox(
            lb_wrap, bg=COLORS["input_bg"], fg=COLORS["text_secondary"],
            selectbackground="#0055CC", selectforeground="#FFFFFF",
            font=("Consolas", 10), borderwidth=0, highlightthickness=0, activestyle="none",
        )
        lb_scroll = ctk.CTkScrollbar(lb_wrap, command=self.listbox_macro.yview)
        self.listbox_macro.configure(yscrollcommand=lb_scroll.set)
        self.listbox_macro.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        lb_scroll.pack(side="right", fill="y", padx=2, pady=4)

        m_btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        m_btn_row.pack(fill="x", pady=(0, 6))
        m_btn_row.grid_columnconfigure(0, weight=3)
        m_btn_row.grid_columnconfigure(1, weight=2)

        self.btn_macro_play = ctk.CTkButton(
            m_btn_row, text="", height=42, corner_radius=10, font=("Segoe UI", 13, "bold"),
            fg_color=COLORS["neon_blue"], hover_color=COLORS["neon_blue_hover"],
            border_width=2, border_color=COLORS["neon_cyan"], text_color="#FFFFFF",
            command=lambda: self.start_macro_playback(from_button=True),
        )
        self.btn_macro_play.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_macro_stop = ctk.CTkButton(
            m_btn_row, text="", height=42, corner_radius=10, font=("Segoe UI", 12, "bold"),
            fg_color=COLORS["danger_bg"], hover_color=COLORS["danger_hover"],
            border_width=2, border_color=COLORS["danger_dim_border"], text_color=COLORS["text_muted"],
            state="disabled", command=self.stop_macro_all,
        )
        self.btn_macro_stop.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.label_macro_footer = ctk.CTkLabel(
            parent, text="", font=("Segoe UI", 10), text_color=COLORS["text_muted"], anchor="w",
        )
        self.label_macro_footer.pack(fill="x")

    # =========================================================================
    # Page 4: Limits, Active Window Filter & Safety
    # =========================================================================
    def _build_page_limits(self, parent: ctk.CTkFrame) -> None:
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 12))
        self.lbl_limits_title = ctk.CTkLabel(hdr, text="", font=("Segoe UI", 16, "bold"), text_color=COLORS["neon_cyan"])
        self.lbl_limits_title.pack(anchor="w")
        self.lbl_limits_sub = ctk.CTkLabel(hdr, text="", font=("Segoe UI", 11), text_color=COLORS["text_secondary"])
        self.lbl_limits_sub.pack(anchor="w")

        c_lim = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        c_lim.pack(fill="x", pady=(0, 12))

        self.lbl_autostop = ctk.CTkLabel(c_lim, text="", font=("Segoe UI", 11, "bold"), text_color=COLORS["neon_cyan"])
        self.lbl_autostop.pack(anchor="w", padx=14, pady=(10, 6))

        self.seg_stop_limit = ctk.CTkSegmentedButton(
            c_lim, values=["Sin límite", "Por ciclos", "Por minutos"],
            command=self._on_stop_limit_change, height=32, corner_radius=8,
            font=("Segoe UI", 11, "bold"), fg_color=COLORS["input_bg"],
            selected_color=COLORS["neon_blue"], selected_hover_color=COLORS["neon_blue_hover"],
            unselected_color=COLORS["input_bg"], unselected_hover_color=COLORS["neon_dim"],
            text_color=COLORS["text_primary"],
        )
        self.seg_stop_limit.set("Sin límite")
        self.seg_stop_limit.pack(fill="x", padx=14, pady=(0, 8))

        lim_row = ctk.CTkFrame(c_lim, fg_color="transparent")
        lim_row.pack(fill="x", padx=14, pady=(0, 12))

        self.lbl_max_cycles = ctk.CTkLabel(lim_row, text="", font=("Segoe UI", 11), text_color=COLORS["text_secondary"])
        self.lbl_max_cycles.pack(side="left")
        self.entry_max_cycles = ctk.CTkEntry(
            lim_row, width=76, height=30, justify="center", font=("Consolas", 13, "bold"),
            fg_color=COLORS["input_bg"], border_width=1, border_color=COLORS["border"], text_color=COLORS["text_primary"],
        )
        self.entry_max_cycles.insert(0, "100")
        self.entry_max_cycles.pack(side="left", padx=(8, 24))

        self.lbl_max_mins = ctk.CTkLabel(lim_row, text="", font=("Segoe UI", 11), text_color=COLORS["text_secondary"])
        self.lbl_max_mins.pack(side="left")
        self.entry_max_minutes = ctk.CTkEntry(
            lim_row, width=76, height=30, justify="center", font=("Consolas", 13, "bold"),
            fg_color=COLORS["input_bg"], border_width=1, border_color=COLORS["border"], text_color=COLORS["text_primary"],
        )
        self.entry_max_minutes.insert(0, "10")
        self.entry_max_minutes.pack(side="left", padx=(8, 0))

        c_cd = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        c_cd.pack(fill="x", pady=(0, 12))

        cd_row = ctk.CTkFrame(c_cd, fg_color="transparent")
        cd_row.pack(fill="x", padx=14, pady=12)

        self.var_countdown = ctk.BooleanVar(value=False)
        self.switch_countdown = ctk.CTkSwitch(
            cd_row, text="", variable=self.var_countdown, command=self._sync_ui_states,
            font=("Segoe UI", 12, "bold"), text_color=COLORS["text_primary"],
            fg_color="#12263F", progress_color=COLORS["neon_blue"], button_color="#6EA4D4",
        )
        self.switch_countdown.pack(side="left")

        self.lbl_sec_short = ctk.CTkLabel(cd_row, text="seg", font=("Segoe UI", 11), text_color=COLORS["text_muted"])
        self.lbl_sec_short.pack(side="right", padx=(6, 0))
        self.entry_countdown = ctk.CTkEntry(
            cd_row, width=56, height=30, justify="center", font=("Consolas", 13, "bold"),
            fg_color=COLORS["input_bg"], border_width=1, border_color=COLORS["border"], text_color=COLORS["text_primary"],
        )
        self.entry_countdown.insert(0, "3")
        self.entry_countdown.pack(side="right")

        c_win = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        c_win.pack(fill="x", pady=(0, 12))

        w_top = ctk.CTkFrame(c_win, fg_color="transparent")
        w_top.pack(fill="x", padx=14, pady=(10, 6))

        self.var_window_filter = ctk.BooleanVar(value=False)
        self.switch_window_filter = ctk.CTkSwitch(
            w_top, text="", variable=self.var_window_filter, command=self._sync_ui_states,
            font=("Segoe UI", 12, "bold"), text_color=COLORS["text_primary"],
            fg_color="#12263F", progress_color=COLORS["neon_blue"], button_color="#6EA4D4",
        )
        self.switch_window_filter.pack(side="left")

        w_pick = ctk.CTkFrame(c_win, fg_color="transparent")
        w_pick.pack(fill="x", padx=14, pady=(0, 12))

        self.opt_window = ctk.CTkOptionMenu(
            w_pick, values=["(Elegir ventana...)"], command=lambda _v: self._sync_ui_states(),
            height=30, corner_radius=8, font=("Segoe UI", 11),
            fg_color=COLORS["input_bg"], button_color=COLORS["neon_dim"],
            button_hover_color=COLORS["neon_blue"], dropdown_fg_color=COLORS["card"],
            text_color=COLORS["text_primary"],
        )
        self.opt_window.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_refresh_win = ctk.CTkButton(
            w_pick, text="", width=102, height=30, corner_radius=8, font=("Segoe UI", 11, "bold"),
            fg_color=COLORS["neon_dim"], hover_color="#12365E", border_width=1,
            border_color=COLORS["border_blue"], text_color=COLORS["neon_cyan"],
            command=self._refresh_open_windows_list,
        )
        self.btn_refresh_win.pack(side="right")

        c_safe = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        c_safe.pack(fill="x")

        self.var_failsafe = ctk.BooleanVar(value=True)
        self.switch_failsafe = ctk.CTkSwitch(
            c_safe, text="", variable=self.var_failsafe, command=self._sync_ui_states,
            font=("Segoe UI", 11, "bold"), text_color=COLORS["text_primary"],
            fg_color="#12263F", progress_color=COLORS["neon_blue"], button_color=COLORS["neon_cyan"],
        )
        self.switch_failsafe.pack(anchor="w", padx=14, pady=(12, 8))

        self.var_sound = ctk.BooleanVar(value=True)
        self.switch_sound = ctk.CTkSwitch(
            c_safe, text="", variable=self.var_sound, command=self._sync_ui_states,
            font=("Segoe UI", 11, "bold"), text_color=COLORS["text_primary"],
            fg_color="#12263F", progress_color=COLORS["neon_blue"], button_color=COLORS["neon_cyan"],
        )
        self.switch_sound.pack(anchor="w", padx=14, pady=(0, 12))

    # =========================================================================
    # Page 5: Profiles, Global Hotkeys & Language
    # =========================================================================
    def _build_page_profiles(self, parent: ctk.CTkFrame) -> None:
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 10))
        self.lbl_profiles_title = ctk.CTkLabel(hdr, text="", font=("Segoe UI", 16, "bold"), text_color=COLORS["neon_cyan"])
        self.lbl_profiles_title.pack(anchor="w")
        self.lbl_profiles_sub = ctk.CTkLabel(hdr, text="", font=("Segoe UI", 11), text_color=COLORS["text_secondary"])
        self.lbl_profiles_sub.pack(anchor="w")

        # Card 1: Profiles
        c_prof = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        c_prof.pack(fill="x", pady=(0, 10))

        self.lbl_presets = ctk.CTkLabel(c_prof, text="", font=("Segoe UI", 11, "bold"), text_color=COLORS["neon_cyan"])
        self.lbl_presets.pack(anchor="w", padx=14, pady=(9, 5))

        save_row = ctk.CTkFrame(c_prof, fg_color="transparent")
        save_row.pack(fill="x", padx=14, pady=(0, 7))

        self.entry_profile_name = ctk.CTkEntry(
            save_row, placeholder_text="", height=30, font=("Segoe UI", 11),
            fg_color=COLORS["input_bg"], border_width=1, border_color=COLORS["border"], text_color=COLORS["text_primary"],
        )
        self.entry_profile_name.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_save_profile = ctk.CTkButton(
            save_row, text="", width=120, height=30, corner_radius=8, font=("Segoe UI", 11, "bold"),
            fg_color=COLORS["neon_blue"], hover_color=COLORS["neon_blue_hover"],
            border_width=1, border_color=COLORS["neon_cyan"], text_color="#FFFFFF",
            command=self._save_named_profile,
        )
        self.btn_save_profile.pack(side="right")

        load_row = ctk.CTkFrame(c_prof, fg_color="transparent")
        load_row.pack(fill="x", padx=14, pady=(0, 8))

        self.opt_profiles = ctk.CTkOptionMenu(
            load_row, values=["(Sin perfiles guardados)"], height=30, corner_radius=8, font=("Segoe UI", 11),
            fg_color=COLORS["input_bg"], button_color=COLORS["neon_dim"],
            button_hover_color=COLORS["neon_blue"], dropdown_fg_color=COLORS["card"], text_color=COLORS["text_primary"],
        )
        self.opt_profiles.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_load_profile = ctk.CTkButton(
            load_row, text="", width=82, height=30, corner_radius=8, font=("Segoe UI", 11, "bold"),
            fg_color=COLORS["neon_dim"], hover_color="#12365E", border_width=1,
            border_color=COLORS["border_blue"], text_color=COLORS["neon_cyan"], command=self._load_named_profile,
        )
        self.btn_load_profile.pack(side="left", padx=(0, 6))

        self.btn_del_profile = ctk.CTkButton(
            load_row, text="", width=82, height=30, corner_radius=8, font=("Segoe UI", 11, "bold"),
            fg_color=COLORS["danger_bg"], hover_color=COLORS["danger_hover"], border_width=1,
            border_color=COLORS["danger_dim_border"], text_color="#FF7A9C", command=self._delete_named_profile,
        )
        self.btn_del_profile.pack(side="left")

        self.label_profile_status = ctk.CTkLabel(c_prof, text="", font=("Segoe UI", 10), text_color=COLORS["text_muted"], anchor="w")
        self.label_profile_status.pack(fill="x", padx=14, pady=(0, 9))

        # Card 2: Customizable Global Hotkeys
        c_hk = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        c_hk.pack(fill="x", pady=(0, 10))

        self.lbl_hotkeys = ctk.CTkLabel(c_hk, text="", font=("Segoe UI", 11, "bold"), text_color=COLORS["accent_sky"])
        self.lbl_hotkeys.pack(anchor="w", padx=14, pady=(9, 6))

        self.lbl_hk_clicker, self.btn_hk_clicker = self._create_hotkey_row(c_hk, "", self.hotkey_clicker_label, "hotkey_clicker")
        self.lbl_hk_record, self.btn_hk_record = self._create_hotkey_row(c_hk, "", self.hotkey_macro_record_label, "hotkey_macro_record")
        self.lbl_hk_play, self.btn_hk_play = self._create_hotkey_row(c_hk, "", self.hotkey_macro_play_label, "hotkey_macro_play")

        self.label_hk_status = ctk.CTkLabel(c_hk, text="", font=("Segoe UI", 10), text_color=COLORS["text_muted"], anchor="w")
        self.label_hk_status.pack(fill="x", padx=14, pady=(4, 9))

        # Card 3: Language Switcher (Spanish default + English)
        c_lang = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["border"])
        c_lang.pack(fill="x")

        lang_row = ctk.CTkFrame(c_lang, fg_color="transparent")
        lang_row.pack(fill="x", padx=14, pady=(10, 6))

        self.lbl_lang_title = ctk.CTkLabel(lang_row, text="IDIOMA / LANGUAGE", font=("Segoe UI", 11, "bold"), text_color=COLORS["neon_cyan"])
        self.lbl_lang_title.pack(side="left")

        self.seg_language = ctk.CTkSegmentedButton(
            lang_row,
            values=["Español", "English"],
            command=self._on_language_change,
            width=210,
            height=30,
            corner_radius=8,
            font=("Segoe UI", 11, "bold"),
            fg_color=COLORS["input_bg"],
            selected_color=COLORS["neon_blue"],
            selected_hover_color=COLORS["neon_blue_hover"],
            unselected_color=COLORS["input_bg"],
            unselected_hover_color=COLORS["neon_dim"],
            text_color=COLORS["text_primary"],
        )
        self.seg_language.set("Español")
        self.seg_language.pack(side="right")

        self.lbl_lang_hint = ctk.CTkLabel(c_lang, text="", font=("Segoe UI", 10), text_color=COLORS["text_muted"], anchor="w")
        self.lbl_lang_hint.pack(fill="x", padx=14, pady=(0, 10))

    def _create_hotkey_row(self, parent, title: str, key_label: str, capture_mode: str) -> Tuple[ctk.CTkLabel, ctk.CTkButton]:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=3)
        lbl = ctk.CTkLabel(row, text=title, font=("Segoe UI", 12), text_color=COLORS["text_primary"])
        lbl.pack(side="left")
        btn = ctk.CTkButton(
            row, text=key_label, width=108, height=28, corner_radius=8, font=("Consolas", 12, "bold"),
            fg_color=COLORS["input_bg"], hover_color=COLORS["neon_dim"], border_width=1,
            border_color=COLORS["border_blue"], text_color=COLORS["neon_cyan"],
            command=lambda m=capture_mode: self._begin_capture(m),
        )
        btn.pack(side="right")
        return lbl, btn

    # =========================================================================
    # Live Language Switching (ES default / EN)
    # =========================================================================
    def _on_language_change(self, choice: str) -> None:
        new_lang = "en" if choice == "English" else "es"
        if new_lang == self.lang:
            return
        self.lang = new_lang
        self._apply_language()
        self._persist_last_state()

    def _apply_language(self) -> None:
        self.title(self._t("window_title"))
        self.lbl_brand_sub.configure(text=self._t("brand_sub"))
        for page_id, t_key in self.NAV_KEYS:
            self.nav_buttons[page_id].configure(text=self._t(t_key))
        self.switch_topmost.configure(text=self._t("pin_window"))
        self.btn_mini_hud.configure(text=self._t("mini_hud_btn"))

        # Page 1: Autoclicker
        self.lbl_control.configure(text=self._t("control_lbl"))
        self.lbl_action_mode.configure(text=self._t("action_mode_lbl"))
        self.seg_input.configure(values=[self._t("input_mouse"), self._t("input_keyboard")])
        self.seg_input.set(self._t("input_keyboard") if self.input_type == "keyboard" else self._t("input_mouse"))

        self.seg_mode.configure(values=[self._t("mode_hold"), self._t("mode_click")])
        self.seg_mode.set(self._t("mode_click") if self.action_mode == "click" else self._t("mode_hold"))

        mb_labels = [self._t("btn_left"), self._t("btn_right"), self._t("btn_middle")]
        self.seg_mouse_btn.configure(values=mb_labels)
        mb_idx = {Button.left: 0, Button.right: 1, Button.middle: 2}.get(self.mouse_button, 0)
        self.seg_mouse_btn.set(mb_labels[mb_idx])

        cc_labels = [self._t("click_single"), self._t("click_double"), self._t("click_triple")]
        self.seg_click_count.configure(values=cc_labels)
        self.seg_click_count.set(cc_labels[max(0, min(2, self.click_count - 1))])

        self.btn_capture_key.configure(text=self._t("capture_key_btn"))
        self.btn_capture_combo.configure(text=self._t("capture_combo_btn"))

        self.card_hold.apply_language(
            title=self._t("chrono_hold_title"),
            unit_min=self._t("unit_min"),
            unit_sec=self._t("unit_sec"),
            unit_ms=self._t("unit_ms"),
            measure_label=self._t("measure_btn"),
            infinite_label=self._t("infinite_hold_lbl"),
            infinite_badge=self._t("infinite_badge"),
        )
        self.card_interval.apply_language(
            title=self._t("chrono_interval_title"),
            unit_min=self._t("unit_min"),
            unit_sec=self._t("unit_sec"),
            unit_ms=self._t("unit_ms"),
            measure_label=self._t("measure_btn"),
        )
        if not self.engine.running:
            self.label_footer_info.configure(text=self._t("footer_ready"), text_color=COLORS["text_muted"])

        # Page 2: Zones
        self.lbl_zones_title.configure(text=self._t("zones_title"))
        self.lbl_zones_sub.configure(text=self._t("zones_sub"))
        self.lbl_cursor_mode.configure(text=self._t("cursor_mode_lbl"))
        cm_vals = [self._t("cm_real"), self._t("cm_both"), self._t("cm_only")]
        self.seg_cursor_mode.configure(values=cm_vals)
        cm_idx = {"real": 0, "both": 1, "target_only": 2}.get(self.cursor_mode, 0)
        self.seg_cursor_mode.set(cm_vals[cm_idx])

        self.lbl_reticle_color.configure(text=self._t("reticle_color_lbl"))
        self.switch_zone_nums.configure(text=self._t("show_nums_lbl"))
        for key, t_key in (
            ("green", "col_green"), ("red", "col_red"), ("cyan", "col_cyan"),
            ("pink", "col_pink"), ("rainbow", "col_rainbow"),
        ):
            self.reticle_color_buttons[key].configure(text=self._t(t_key))
        self.btn_reticle_custom.configure(text=self._t("col_custom"))
        self.btn_add_zone.configure(text=self._t("add_zone_btn"))
        self.btn_zone_reset.configure(text=self._t("reset_btn"))
        self.btn_toggle_zone.configure(
            text=self._t("hide_zones_btn") if self.zones_visible else self._t("show_zones_btn")
        )
        self.lbl_zones_tip.configure(text=self._t("zones_tip"))
        self._rebuild_zones_list_ui()

        # Page 3: Macros
        self.chk_rec_kb.configure(text=self._t("chk_kb"))
        self.chk_rec_mouse.configure(text=self._t("chk_mouse"))
        self.chk_rec_moves.configure(text=self._t("chk_moves"))
        self.lbl_library.configure(text=self._t("library_lbl"))
        self.btn_macro_save.configure(text=self._t("save_btn"))
        self.btn_macro_load.configure(text=self._t("open_btn"))
        self.btn_macro_clear.configure(text=self._t("clear_btn"))
        self.lbl_rep.configure(text=self._t("rep_lbl"))
        self.lbl_pause_s.configure(text=self._t("pause_s_lbl"))
        self.lbl_step_editor.configure(text=self._t("step_editor_lbl"))
        self.btn_step_pause.configure(text=self._t("add_pause_btn"))
        self.btn_step_delay.configure(text=self._t("edit_wait_btn"))
        self.btn_step_delete.configure(text=self._t("del_step_btn"))
        self.label_macro_footer.configure(text=self._t("macro_footer_hint"), text_color=COLORS["text_muted"])
        self._refresh_macro_library()
        self._render_macro_list()

        # Page 4: Limits & Safety
        self.lbl_limits_title.configure(text=self._t("limits_title"))
        self.lbl_limits_sub.configure(text=self._t("limits_sub"))
        self.lbl_autostop.configure(text=self._t("autostop_lbl"))
        lim_vals = [self._t("lim_none"), self._t("lim_cycles"), self._t("lim_minutes")]
        self.seg_stop_limit.configure(values=lim_vals)
        lim_idx = {"none": 0, "cycles": 1, "minutes": 2}.get(self.stop_limit_mode, 0)
        self.seg_stop_limit.set(lim_vals[lim_idx])
        self.lbl_max_cycles.configure(text=self._t("max_cycles_lbl"))
        self.lbl_max_mins.configure(text=self._t("max_mins_lbl"))
        self.switch_countdown.configure(text=self._t("countdown_lbl"))
        self.lbl_sec_short.configure(text=self._t("sec_short"))
        self.switch_window_filter.configure(text=self._t("win_filter_lbl"))
        self.btn_refresh_win.configure(text=self._t("refresh_btn"))
        self.switch_failsafe.configure(text=self._t("failsafe_lbl"))
        self.switch_sound.configure(text=self._t("sound_lbl"))
        self._refresh_open_windows_list()

        # Page 5: Profiles, Hotkeys & Language
        self.lbl_profiles_title.configure(text=self._t("profiles_title"))
        self.lbl_profiles_sub.configure(text=self._t("profiles_sub"))
        self.lbl_presets.configure(text=self._t("presets_lbl"))
        self.entry_profile_name.configure(placeholder_text=self._t("profile_placeholder"))
        self.btn_save_profile.configure(text=self._t("save_profile_btn"))
        self.btn_load_profile.configure(text=self._t("load_btn"))
        self.btn_del_profile.configure(text=self._t("delete_btn"))
        self.label_profile_status.configure(text=self._t("profile_hint"), text_color=COLORS["text_muted"])
        self.lbl_hotkeys.configure(text=self._t("hotkeys_lbl"))
        self.lbl_hk_clicker.configure(text=self._t("hk_clicker_row"))
        self.lbl_hk_record.configure(text=self._t("hk_record_row"))
        self.lbl_hk_play.configure(text=self._t("hk_play_row"))
        self.label_hk_status.configure(text=self._t("hk_hint"), text_color=COLORS["text_muted"])
        self.lbl_lang_title.configure(text=self._t("lang_card_lbl"))
        self.lbl_lang_hint.configure(text=self._t("lang_hint"))
        self.seg_language.set("English" if self.lang == "en" else "Español")
        self._refresh_profile_dropdown()

        self._refresh_hotkey_labels()
        self._sync_ui_states()
        self._sync_macro_ui()

    # =========================================================================
    # Multi-Zone Holographic Reticle Management
    # =========================================================================
    def _get_live_zones(self) -> Tuple[Tuple[int, int], ...]:
        if self.zones_visible and self.zone_overlays:
            updated: List[Tuple[int, int]] = []
            for ov in self.zone_overlays:
                if ov.winfo_exists():
                    updated.append(ov.get_center_pos())
            if updated:
                self.zone_coords = updated
        return tuple(self.zone_coords)

    def _set_reticle_color(self, color_spec: str) -> None:
        self.reticle_color = color_spec
        self._refresh_reticle_color_buttons()
        self._apply_reticle_colors_now()
        self._rebuild_zones_list_ui()

    def _pick_custom_reticle_color(self) -> None:
        cur_main, _ = resolve_reticle_colors(self.reticle_color)
        _, hex_col = colorchooser.askcolor(color=cur_main, title=self._t("color_picker_title"), parent=self)
        if hex_col:
            self._set_reticle_color(hex_col.upper())

    def _on_reticle_numbers_toggle(self) -> None:
        self.reticle_show_numbers = self.var_show_zone_nums.get()
        self.switch_zone_nums.configure(button_color=COLORS["neon_cyan"] if self.reticle_show_numbers else "#6EA4D4")
        for idx, ov in enumerate(self.zone_overlays):
            if ov.winfo_exists():
                ov.set_index_label((idx + 1) if self.reticle_show_numbers else None)

    def _refresh_reticle_color_buttons(self) -> None:
        swatches = {"green": "#39FF14", "red": "#FF2A4B", "cyan": "#00F0FF", "pink": "#FF3ED6", "rainbow": "#FDE047"}
        is_custom = self.reticle_color not in swatches
        for key, btn in self.reticle_color_buttons.items():
            swatch = swatches[key]
            selected = self.reticle_color == key
            btn.configure(
                fg_color="#112845" if selected else COLORS["input_bg"],
                border_color=swatch if selected else COLORS["border"],
                border_width=2 if selected else 1,
                text_color=swatch,
            )
        if is_custom:
            main_hex, _ = resolve_reticle_colors(self.reticle_color)
            self.btn_reticle_custom.configure(fg_color="#112845", border_color=main_hex, border_width=2, text_color=main_hex)
        else:
            self.btn_reticle_custom.configure(fg_color=COLORS["input_bg"], border_color=COLORS["border"], border_width=1, text_color=COLORS["text_secondary"])

    def _apply_reticle_colors_now(self) -> None:
        base_phase = (time.perf_counter() * 0.28) % 1.0
        for idx, ov in enumerate(self.zone_overlays):
            if ov.winfo_exists():
                phase = (base_phase + idx * 0.12) % 1.0
                main_hex, glow_hex = resolve_reticle_colors(self.reticle_color, phase=phase)
                ov.set_color(main_hex, glow_hex)

    def _rebuild_zones_list_ui(self) -> None:
        for child in self.zones_scroll.winfo_children():
            child.destroy()
        self._zone_coord_labels = []
        total = len(self.zone_coords)
        self.label_zones_count.configure(text=self._t("zones_active_lbl", n=total))
        badge_main, _ = resolve_reticle_colors(self.reticle_color, phase=0.33)
        self._zone_index_badges: List[ctk.CTkLabel] = []

        for idx, (zx, zy) in enumerate(self.zone_coords):
            row = ctk.CTkFrame(self.zones_scroll, fg_color=COLORS["card"], corner_radius=8, border_width=1, border_color=COLORS["border_dim"])
            row.pack(fill="x", padx=4, pady=3)

            if self.reticle_color == "rainbow":
                b_fg, _ = resolve_reticle_colors("rainbow", phase=(idx * 0.16) % 1.0)
            else:
                b_fg = badge_main

            lbl_idx = ctk.CTkLabel(
                row, text=f"#{idx + 1}", font=("Consolas", 11, "bold"), text_color=b_fg,
                fg_color=COLORS["card_inner"], corner_radius=6, width=34, height=24,
            )
            lbl_idx.pack(side="left", padx=(8, 10), pady=6)
            self._zone_index_badges.append(lbl_idx)

            lbl_coord = ctk.CTkLabel(row, text=f"X: {zx:4d}    Y: {zy:4d}", font=("Consolas", 12, "bold"), text_color=COLORS["text_primary"])
            lbl_coord.pack(side="left")
            self._zone_coord_labels.append(lbl_coord)

            ctk.CTkButton(
                row, text="✕", width=28, height=24, corner_radius=6, font=("Segoe UI", 10, "bold"),
                fg_color=COLORS["danger_bg"] if total > 1 else COLORS["card_dim"],
                hover_color=COLORS["danger_hover"], text_color="#FF7A9C" if total > 1 else COLORS["text_muted"],
                state="normal" if total > 1 else "disabled", command=lambda i=idx: self._remove_zone(i),
            ).pack(side="right", padx=(3, 8))

            ctk.CTkButton(
                row, text="↓", width=26, height=24, corner_radius=6, font=("Segoe UI", 10, "bold"),
                fg_color=COLORS["neon_dim"], hover_color="#12365E", text_color=COLORS["text_secondary"],
                state="normal" if idx < total - 1 else "disabled", command=lambda i=idx: self._swap_zones(i, i + 1),
            ).pack(side="right", padx=2)

            ctk.CTkButton(
                row, text="↑", width=26, height=24, corner_radius=6, font=("Segoe UI", 10, "bold"),
                fg_color=COLORS["neon_dim"], hover_color="#12365E", text_color=COLORS["text_secondary"],
                state="normal" if idx > 0 else "disabled", command=lambda i=idx: self._swap_zones(i, i - 1),
            ).pack(side="right", padx=2)

    def _on_single_overlay_moved(self, idx: int, x: int, y: int) -> None:
        if 0 <= idx < len(self.zone_coords):
            self.zone_coords[idx] = (x, y)
            if idx < len(self._zone_coord_labels):
                self._zone_coord_labels[idx].configure(text=f"X: {x:4d}    Y: {y:4d}")

    def _show_zone_overlays(self) -> None:
        self._destroy_zone_overlays()
        self.zones_visible = True
        base_phase = (time.perf_counter() * 0.28) % 1.0
        for idx, (zx, zy) in enumerate(self.zone_coords):
            phase = (base_phase + idx * 0.12) % 1.0
            main_hex, glow_hex = resolve_reticle_colors(self.reticle_color, phase=phase)
            ov = TargetMarkerWindow(
                self, initial_pos=(zx, zy),
                index_label=(idx + 1) if self.reticle_show_numbers else None,
                main_color=main_hex, glow_color=glow_hex,
                on_move=lambda x, y, i=idx: self._on_single_overlay_moved(i, x, y),
            )
            ov.set_click_through(self.engine.running)
            self.zone_overlays.append(ov)
        self.btn_toggle_zone.configure(
            text=self._t("hide_zones_btn"), fg_color=COLORS["holo_green"],
            border_color="#8CFF75", text_color="#041005",
        )

    def _destroy_zone_overlays(self) -> None:
        for ov in self.zone_overlays:
            try:
                if ov.winfo_exists():
                    ov.destroy()
            except Exception:
                pass
        self.zone_overlays = []

    def _hide_zone_overlays(self) -> None:
        self._get_live_zones()
        self._destroy_zone_overlays()
        self.zones_visible = False
        self.btn_toggle_zone.configure(
            text=self._t("show_zones_btn"), fg_color=COLORS["holo_green_dim"],
            border_color=COLORS["holo_green"], text_color=COLORS["holo_green"],
        )

    def _toggle_zone_overlays(self) -> None:
        if self.zones_visible:
            self._hide_zone_overlays()
        else:
            self._show_zone_overlays()

    def _add_zone(self) -> None:
        if self.engine.running:
            return
        self._get_live_zones()
        lx, ly = self.zone_coords[-1]
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.zone_coords.append((min(sw - 60, max(60, lx + 70)), min(sh - 60, max(60, ly + 40))))
        self._rebuild_zones_list_ui()
        if self.zones_visible or self.cursor_mode != "real":
            self._show_zone_overlays()
        self._sync_ui_states()

    def _remove_zone(self, idx: int) -> None:
        if self.engine.running or len(self.zone_coords) <= 1:
            return
        self._get_live_zones()
        self.zone_coords.pop(idx)
        self._rebuild_zones_list_ui()
        if self.zones_visible:
            self._show_zone_overlays()
        self._sync_ui_states()

    def _swap_zones(self, i: int, j: int) -> None:
        if self.engine.running:
            return
        self._get_live_zones()
        self.zone_coords[i], self.zone_coords[j] = self.zone_coords[j], self.zone_coords[i]
        self._rebuild_zones_list_ui()
        if self.zones_visible:
            self._show_zone_overlays()

    def _reset_zones(self) -> None:
        if self.engine.running:
            return
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.zone_coords = [(sw // 2, sh // 2)]
        self._rebuild_zones_list_ui()
        if self.zones_visible:
            self._show_zone_overlays()
        self._sync_ui_states()

    def _on_cursor_mode_change(self, choice: str) -> None:
        if choice in ("Mouse real", "Real mouse"):
            self.cursor_mode = "real"
        elif choice in ("Solo zonas fijas", "Zones only"):
            self.cursor_mode = "target_only"
            self._show_zone_overlays()
        else:
            self.cursor_mode = "both"
            self._show_zone_overlays()
        self._sync_ui_states()

    # =========================================================================
    # Mini-HUD Floating Window (Zero-Flash Alpha-Buffered Transitions)
    # =========================================================================
    def _reveal_main_window(self) -> None:
        try:
            if self.winfo_exists():
                self.attributes("-alpha", 1.0)
        except Exception:
            pass

    def _on_mini_hud_toggle(self) -> None:
        if self.macro_recorder.recording or self.macro_player.running:
            self.stop_macro_all()
        elif self.engine.running or self._pending_start_after is not None:
            self.stop_clicking()
        elif self.active_page == "macros" and self.macro_events:
            self.start_macro_playback(from_button=False)
        else:
            self.start_clicking(from_button=False)

    def _open_mini_hud(self) -> None:
        if self.mini_hud is not None and self.mini_hud.winfo_exists():
            self.mini_hud.lift()
            return
        self.mini_hud = MiniHudWindow(self, on_toggle=self._on_mini_hud_toggle, on_restore=self._close_mini_hud)
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.withdraw()

    def _close_mini_hud(self) -> None:
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.deiconify()
        self.lift()
        self.update_idletasks()
        self.update()
        if self.mini_hud is not None and self.mini_hud.winfo_exists():
            self.mini_hud.destroy()
        self.mini_hud = None
        self.after(45, self._reveal_main_window)

    # =========================================================================
    # State Sync, Config Persistence & Profiles
    # =========================================================================
    def _toggle_topmost(self) -> None:
        is_top = self.topmost_var.get()
        self.attributes("-topmost", is_top)
        self.switch_topmost.configure(
            button_color=COLORS["neon_cyan"] if is_top else "#6EA4D4",
            text_color=COLORS["neon_cyan"] if is_top else COLORS["text_secondary"],
        )

    def _on_input_type_change(self, choice: str) -> None:
        self.input_type = "mouse" if choice == "Mouse" else "keyboard"
        self._sync_ui_states()

    def _on_action_mode_change(self, choice: str) -> None:
        self.action_mode = "hold" if choice in ("Mantener", "Hold") else "click"
        self._sync_ui_states()

    def _on_mouse_button_change(self, choice: str) -> None:
        mapping = {
            "Izquierdo": Button.left, "Left": Button.left,
            "Derecho": Button.right, "Right": Button.right,
            "Medio": Button.middle, "Middle": Button.middle,
        }
        self.mouse_button = mapping.get(choice, Button.left)

    def _on_click_count_change(self, choice: str) -> None:
        mapping = {"Simple": 1, "Single": 1, "Doble": 2, "Double": 2, "Triple": 3}
        self.click_count = mapping.get(choice, 1)

    def _on_stop_limit_change(self, choice: str) -> None:
        if choice in ("Por ciclos", "By cycles"):
            self.stop_limit_mode = "cycles"
        elif choice in ("Por minutos", "By minutes"):
            self.stop_limit_mode = "minutes"
        else:
            self.stop_limit_mode = "none"
        self._sync_ui_states()

    def _on_infinite_hold_change(self, _is_inf: bool) -> None:
        self._sync_ui_states()

    def _is_window_placeholder(self, text: str) -> bool:
        return text in ("(Elegir ventana...)", "(Choose window...)", "")

    def _refresh_open_windows_list(self) -> None:
        placeholder = self._t("choose_win")
        wins = list_open_windows()
        values = [placeholder] + wins
        current = self.opt_window.get()
        self.opt_window.configure(values=values)
        if not self._is_window_placeholder(current) and current in values:
            self.opt_window.set(current)
        else:
            self.opt_window.set(placeholder)

    def _collect_state_dict(self) -> Dict[str, Any]:
        self._get_live_zones()
        try:
            max_cyc = max(1, int(self.entry_max_cycles.get().strip()))
        except ValueError:
            max_cyc = 100
        try:
            max_min = max(0.1, float(self.entry_max_minutes.get().strip().replace(",", ".")))
        except ValueError:
            max_min = 10.0
        try:
            cd_sec = max(1, int(self.entry_countdown.get().strip()))
        except ValueError:
            cd_sec = 3

        win_choice = self.opt_window.get()
        if self._is_window_placeholder(win_choice):
            win_choice = ""

        return {
            "language": self.lang,
            "input_type": self.input_type,
            "action_mode": self.action_mode,
            "mouse_button": MOUSE_BTN_TO_STR.get(self.mouse_button, "left"),
            "click_count": self.click_count,
            "target_keys": [serialize_key(k) for k in self.target_keys],
            "hold_seconds": self.card_hold.get_seconds(),
            "interval_seconds": self.card_interval.get_seconds(),
            "infinite_hold": self.card_hold.infinite_var.get(),
            "cursor_mode": self.cursor_mode,
            "zones": [[int(x), int(y)] for x, y in self.zone_coords],
            "reticle_color": self.reticle_color,
            "reticle_show_numbers": self.reticle_show_numbers,
            "stop_limit_mode": self.stop_limit_mode,
            "stop_max_cycles": max_cyc,
            "stop_max_minutes": max_min,
            "countdown_enabled": self.var_countdown.get(),
            "countdown_seconds": cd_sec,
            "window_filter_enabled": self.var_window_filter.get(),
            "target_window_title": win_choice,
            "failsafe_enabled": self.var_failsafe.get(),
            "sound_enabled": self.var_sound.get(),
            "topmost": self.topmost_var.get(),
            "hotkey_clicker": serialize_key(self.hotkey_clicker),
            "hotkey_macro_record": serialize_key(self.hotkey_macro_record),
            "hotkey_macro_play": serialize_key(self.hotkey_macro_play),
        }

    def _apply_state_dict(self, state: Dict[str, Any], show_overlays: bool = True) -> None:
        self.lang = "en" if state.get("language") == "en" else "es"
        self.input_type = "keyboard" if state.get("input_type") == "keyboard" else "mouse"
        self.action_mode = "click" if state.get("action_mode") == "click" else "hold"

        mb_str = str(state.get("mouse_button", "left"))
        self.mouse_button = MOUSE_STR_TO_BTN.get(mb_str, Button.left)
        self.click_count = max(1, min(3, int(state.get("click_count", 1))))

        raw_keys = state.get("target_keys", [])
        loaded_keys: List[Union[Key, KeyCode]] = []
        for item in raw_keys:
            obj, _ = deserialize_key(item)
            if obj is not None:
                loaded_keys.append(obj)
        self.target_keys = loaded_keys if loaded_keys else [Key.space]
        self.label_key_badge.configure(text=f"[ {format_combo_label(self.target_keys)} ]")

        self.card_hold.infinite_var.set(bool(state.get("infinite_hold", False)))
        self.card_hold.set_seconds(float(state.get("hold_seconds", 1.5)))
        self.card_interval.set_seconds(float(state.get("interval_seconds", 1.0)))

        cm = str(state.get("cursor_mode", "real"))
        self.cursor_mode = cm if cm in ("real", "both", "target_only") else "real"

        raw_zones = state.get("zones", [])
        parsed_zones = [(int(p[0]), int(p[1])) for p in raw_zones if isinstance(p, (list, tuple)) and len(p) == 2]
        if parsed_zones:
            self.zone_coords = parsed_zones
        self.reticle_color = str(state.get("reticle_color", "green"))
        self.reticle_show_numbers = bool(state.get("reticle_show_numbers", True))
        self.var_show_zone_nums.set(self.reticle_show_numbers)
        self.switch_zone_nums.configure(button_color=COLORS["neon_cyan"] if self.reticle_show_numbers else "#6EA4D4")
        self._refresh_reticle_color_buttons()

        if show_overlays and self.cursor_mode != "real":
            self._show_zone_overlays()
        elif not show_overlays:
            self._hide_zone_overlays()

        slm = str(state.get("stop_limit_mode", "none"))
        self.stop_limit_mode = slm if slm in ("none", "cycles", "minutes") else "none"
        self.entry_max_cycles.delete(0, "end")
        self.entry_max_cycles.insert(0, str(state.get("stop_max_cycles", 100)))
        self.entry_max_minutes.delete(0, "end")
        self.entry_max_minutes.insert(0, str(state.get("stop_max_minutes", 10)))

        self.var_countdown.set(bool(state.get("countdown_enabled", False)))
        self.entry_countdown.delete(0, "end")
        self.entry_countdown.insert(0, str(state.get("countdown_seconds", 3)))

        self.var_window_filter.set(bool(state.get("window_filter_enabled", False)))
        saved_win = str(state.get("target_window_title", ""))
        if saved_win:
            vals = list(self.opt_window.cget("values"))
            if saved_win not in vals:
                vals.append(saved_win)
                self.opt_window.configure(values=vals)
            self.opt_window.set(saved_win)

        self.var_failsafe.set(bool(state.get("failsafe_enabled", True)))
        self.var_sound.set(bool(state.get("sound_enabled", True)))
        self.topmost_var.set(bool(state.get("topmost", False)))
        self._toggle_topmost()

        for attr_key, attr_lbl, field_name, fallback in (
            ("hotkey_clicker", "hotkey_clicker_label", "hotkey_clicker", (Key.f6, "F6")),
            ("hotkey_macro_record", "hotkey_macro_record_label", "hotkey_macro_record", (Key.f7, "F7")),
            ("hotkey_macro_play", "hotkey_macro_play_label", "hotkey_macro_play", (Key.f8, "F8")),
        ):
            obj, lbl = deserialize_key(state.get(field_name, {}))
            if obj is None:
                obj, lbl = fallback
            setattr(self, attr_key, obj)
            setattr(self, attr_lbl, lbl)

        self._apply_language()

    def _refresh_hotkey_labels(self) -> None:
        self.btn_start.configure(text=self._t("start_btn", hk=self.hotkey_clicker_label))
        self.btn_stop.configure(text=self._t("stop_btn", hk=self.hotkey_clicker_label))
        self.btn_macro_play.configure(text=self._t("macro_play_btn", hk=self.hotkey_macro_play_label))
        self.btn_macro_stop.configure(text=self._t("macro_stop_btn", hk=self.hotkey_macro_play_label))
        if not self.macro_recorder.recording:
            self.btn_macro_record.configure(text=self._t("macro_rec_btn", hk=self.hotkey_macro_record_label))
        self.btn_hk_clicker.configure(text=self.hotkey_clicker_label)
        self.btn_hk_record.configure(text=self.hotkey_macro_record_label)
        self.btn_hk_play.configure(text=self.hotkey_macro_play_label)

    def _persist_last_state(self) -> None:
        try:
            self.config_data["last_state"] = self._collect_state_dict()
            save_config_file(self.config_data, self._config_path)
        except Exception:
            pass

    def _refresh_profile_dropdown(self) -> None:
        names = sorted(self.config_data.get("profiles", {}).keys(), key=str.lower)
        values = names if names else [self._t("no_profiles")]
        current = self.opt_profiles.get()
        self.opt_profiles.configure(values=values)
        self.opt_profiles.set(current if current in values else values[0])

    def _save_named_profile(self) -> None:
        name = self.entry_profile_name.get().strip()
        if not name:
            self.label_profile_status.configure(text=self._t("profile_err_name"), text_color=COLORS["danger"])
            return
        self.config_data.setdefault("profiles", {})[name] = self._collect_state_dict()
        self._persist_last_state()
        self._refresh_profile_dropdown()
        self.opt_profiles.set(name)
        self.entry_profile_name.delete(0, "end")
        self.label_profile_status.configure(text=self._t("profile_saved", name=name), text_color=COLORS["neon_cyan"])

    def _load_named_profile(self) -> None:
        if self.engine.running:
            self.label_profile_status.configure(text=self._t("profile_err_running"), text_color=COLORS["danger"])
            return
        name = self.opt_profiles.get()
        prof = self.config_data.get("profiles", {}).get(name)
        if not prof:
            self.label_profile_status.configure(text=self._t("profile_err_none"), text_color=COLORS["danger"])
            return
        self._apply_state_dict(prof, show_overlays=True)
        self._persist_last_state()
        self.label_profile_status.configure(text=self._t("profile_loaded", name=name), text_color=COLORS["neon_cyan"])

    def _delete_named_profile(self) -> None:
        name = self.opt_profiles.get()
        profiles = self.config_data.get("profiles", {})
        if name not in profiles:
            return
        del profiles[name]
        self._persist_last_state()
        self._refresh_profile_dropdown()
        self.label_profile_status.configure(text=self._t("profile_deleted", name=name), text_color=COLORS["text_secondary"])

    def _sync_ui_states(self) -> None:
        running = self.engine.running or self._pending_start_after is not None

        if self.input_type == "mouse":
            self.frame_key_target.pack_forget()
            self.frame_mouse_target.pack(fill="x")
        else:
            self.frame_mouse_target.pack_forget()
            self.frame_key_target.pack(fill="x")

        is_mouse = self.input_type == "mouse"
        is_hold = self.action_mode == "hold"
        is_inf = is_hold and self.card_hold.infinite_var.get()

        ctrl_state = "disabled" if running else "normal"
        self.seg_input.configure(state=ctrl_state)
        self.seg_mode.configure(state=ctrl_state)
        self.seg_mouse_btn.configure(state=ctrl_state)
        self.seg_click_count.configure(state="normal" if (not running and not is_hold) else "disabled")
        self.btn_capture_key.configure(state=ctrl_state)
        self.btn_capture_combo.configure(state=ctrl_state)
        self.seg_cursor_mode.configure(state="normal" if (is_mouse and not running) else "disabled")

        if not is_mouse:
            self.label_zone_hint.configure(text=self._t("zone_hint_kb"))
        elif self.cursor_mode == "real":
            self.label_zone_hint.configure(text=self._t("zone_hint_real"))
        elif self.cursor_mode == "both":
            self.label_zone_hint.configure(text=self._t("zone_hint_both", n=len(self.zone_coords)))
        else:
            self.label_zone_hint.configure(text=self._t("zone_hint_only", n=len(self.zone_coords)))

        if not is_hold:
            self.card_hold.set_inputs_enabled(False, allow_infinite_switch=False)
            self.card_hold.set_subtitle(self._t("sub_hold_only"))
        elif is_inf:
            self.card_hold.set_inputs_enabled(False, allow_infinite_switch=not running)
            self.card_hold.set_subtitle(self._t("sub_hold_inf", hk=self.hotkey_clicker_label))
        else:
            self.card_hold.set_inputs_enabled(not running, allow_infinite_switch=not running)
            self.card_hold.set_subtitle(self._t("sub_hold_normal"))

        if is_inf:
            self.card_interval.set_inputs_enabled(False)
            self.card_interval.set_subtitle(self._t("sub_int_inf"))
        elif is_hold:
            self.card_interval.set_inputs_enabled(not running)
            self.card_interval.set_subtitle(self._t("sub_int_hold"))
        else:
            self.card_interval.set_inputs_enabled(not running)
            self.card_interval.set_subtitle(self._t("sub_int_click"))

        self.entry_max_cycles.configure(state="normal" if (not running and self.stop_limit_mode == "cycles") else "disabled")
        self.entry_max_minutes.configure(state="normal" if (not running and self.stop_limit_mode == "minutes") else "disabled")
        self.entry_countdown.configure(state="normal" if (not running and self.var_countdown.get()) else "disabled")
        self.opt_window.configure(state="normal" if (not running and self.var_window_filter.get()) else "disabled")
        for sw, var in (
            (self.switch_countdown, self.var_countdown),
            (self.switch_window_filter, self.var_window_filter),
            (self.switch_failsafe, self.var_failsafe),
            (self.switch_sound, self.var_sound),
        ):
            sw.configure(button_color=COLORS["neon_cyan"] if var.get() else "#6EA4D4")

        badges: List[str] = []
        if is_mouse and self.cursor_mode != "real":
            z_tag = self._t("badge_zones_only") if self.cursor_mode == "target_only" else self._t("badge_zones_both")
            badges.append(f"◎ {z_tag} ({len(self.zone_coords)})")
        if self.stop_limit_mode == "cycles":
            badges.append(f"⏱ {self.entry_max_cycles.get()}c")
        elif self.stop_limit_mode == "minutes":
            badges.append(f"⏱ {self.entry_max_minutes.get()}m")
        if self.var_window_filter.get() and not self._is_window_placeholder(self.opt_window.get()):
            badges.append(self._t("badge_filter"))
        self.label_hud_badges.configure(text="  ".join(badges))

        in_txt = self._t("input_mouse") if is_mouse else format_combo_label(self.target_keys)
        md_txt = self._t("mode_hold") if is_hold else (
            self._t("mode_click") if self.click_count == 1 else f"{self._t('mode_click')} x{self.click_count}"
        )
        self.label_sb_sub.configure(text=f"{in_txt} · {md_txt}")

        if running:
            self.btn_start.configure(state="disabled", fg_color="#091C36", border_color=COLORS["border"], text_color=COLORS["text_muted"])
            self.btn_stop.configure(state="normal", fg_color=COLORS["danger_bg"], border_color=COLORS["danger"], text_color="#FF6B8E")
        else:
            self.btn_start.configure(state="normal", fg_color=COLORS["neon_blue"], border_color=COLORS["neon_cyan"], text_color="#FFFFFF")
            self.btn_stop.configure(state="disabled", fg_color="#10080D", border_color=COLORS["danger_dim_border"], text_color=COLORS["text_muted"])

        if not running:
            if is_inf:
                self.label_clock_target.configure(text="/ ∞")
            elif is_hold:
                self.label_clock_target.configure(text=f"/ {format_timer(self.card_hold.get_seconds())}")
            else:
                self.label_clock_target.configure(text=f"/ {format_timer(self.card_interval.get_seconds())}")

    # =========================================================================
    # Global Hotkey & Combo Capture
    # =========================================================================
    def _begin_capture(self, mode: str) -> None:
        if self.engine.running or self.macro_recorder.recording or self.macro_player.running:
            return
        self.capturing_mode = mode
        self._held_modifiers.clear()
        if mode == "target_single":
            self.btn_capture_key.configure(text=self._t("capturing_key_btn"), fg_color=COLORS["neon_blue"], text_color="#FFFFFF")
            self.label_footer_info.configure(text=self._t("cap_key_prompt"), text_color=COLORS["neon_cyan"])
        elif mode == "target_combo":
            self.btn_capture_combo.configure(text=self._t("capturing_combo_btn"), fg_color=COLORS["neon_blue"], text_color="#FFFFFF")
            self.label_footer_info.configure(text=self._t("cap_combo_prompt"), text_color=COLORS["neon_cyan"])
        elif mode == "hotkey_clicker":
            self.btn_hk_clicker.configure(text="...", fg_color=COLORS["neon_blue"], text_color="#FFFFFF")
            self.label_hk_status.configure(text=self._t("hk_prompt_clicker"), text_color=COLORS["neon_cyan"])
        elif mode == "hotkey_macro_record":
            self.btn_hk_record.configure(text="...", fg_color=COLORS["neon_blue"], text_color="#FFFFFF")
            self.label_hk_status.configure(text=self._t("hk_prompt_rec"), text_color=COLORS["neon_cyan"])
        elif mode == "hotkey_macro_play":
            self.btn_hk_play.configure(text="...", fg_color=COLORS["neon_blue"], text_color="#FFFFFF")
            self.label_hk_status.configure(text=self._t("hk_prompt_play"), text_color=COLORS["neon_cyan"])

    def _on_global_key_press(self, key: Union[Key, KeyCode]) -> None:
        mod = _canonical_modifier(key)
        if mod is not None and mod not in self._held_modifiers:
            self._held_modifiers.append(mod)

        if self.capturing_mode is not None:
            mode = self.capturing_mode
            mods_snapshot = list(self._held_modifiers)
            self.after(0, lambda k=key, m=mode, ms=mods_snapshot: self._handle_capture_key(k, m, ms))
            return

        if same_key(key, self.hotkey_macro_record):
            if "rec" in self._held_hotkeys:
                return
            self._held_hotkeys.add("rec")
            self.after(0, lambda: self.toggle_macro_recording(from_button=False))
            return
        if same_key(key, self.hotkey_macro_play):
            if "play" in self._held_hotkeys:
                return
            self._held_hotkeys.add("play")
            self.after(0, self.toggle_macro_playback)
            return
        if same_key(key, self.hotkey_clicker):
            if "clicker" in self._held_hotkeys:
                return
            self._held_hotkeys.add("clicker")
            self.after(0, self.toggle_clicking)

    def _on_global_key_release(self, key: Union[Key, KeyCode]) -> None:
        mod = _canonical_modifier(key)
        if mod is not None and mod in self._held_modifiers:
            self._held_modifiers.remove(mod)
        if same_key(key, self.hotkey_macro_record):
            self._held_hotkeys.discard("rec")
        if same_key(key, self.hotkey_macro_play):
            self._held_hotkeys.discard("play")
        if same_key(key, self.hotkey_clicker):
            self._held_hotkeys.discard("clicker")

    def _reset_capture_buttons(self) -> None:
        self.btn_capture_key.configure(text=self._t("capture_key_btn"), fg_color=COLORS["neon_dim"], text_color=COLORS["neon_cyan"])
        self.btn_capture_combo.configure(text=self._t("capture_combo_btn"), fg_color=COLORS["neon_dim"], text_color=COLORS["text_primary"])
        self.btn_hk_clicker.configure(fg_color=COLORS["input_bg"], text_color=COLORS["neon_cyan"])
        self.btn_hk_record.configure(fg_color=COLORS["input_bg"], text_color=COLORS["neon_cyan"])
        self.btn_hk_play.configure(fg_color=COLORS["input_bg"], text_color=COLORS["neon_cyan"])
        self._refresh_hotkey_labels()

    def _handle_capture_key(self, raw_key: Union[Key, KeyCode], mode: str, mods_snapshot: List[Key]) -> None:
        if self.capturing_mode != mode:
            return
        if raw_key == Key.esc:
            self.capturing_mode = None
            self._reset_capture_buttons()
            self.label_footer_info.configure(text=self._t("cap_cancelled"), text_color=COLORS["text_muted"])
            self.label_hk_status.configure(text=self._t("hk_cancelled"), text_color=COLORS["text_muted"])
            return

        if mode == "target_combo" and raw_key in MODIFIER_KEYS:
            preview = " + ".join(normalize_captured_key(m)[1] for m in mods_snapshot)
            self.label_footer_info.configure(text=self._t("cap_combo_holding", mods=preview), text_color=COLORS["neon_cyan"])
            return

        key_obj, label = normalize_captured_key(raw_key)
        self.capturing_mode = None
        self._reset_capture_buttons()

        if mode in ("target_single", "target_combo"):
            if mode == "target_single" and any(
                same_key(key_obj, hk) for hk in (self.hotkey_clicker, self.hotkey_macro_record, self.hotkey_macro_play)
            ):
                self.label_footer_info.configure(text=self._t("cap_reserved", lbl=label), text_color=COLORS["danger"])
                return
            combo = list(mods_snapshot) + [key_obj] if mode == "target_combo" else [key_obj]
            dedup: List[Union[Key, KeyCode]] = []
            for k in combo:
                if not any(same_key(k, existing) for existing in dedup):
                    dedup.append(k)
            self.target_keys = dedup
            combo_lbl = format_combo_label(self.target_keys)
            self.label_key_badge.configure(text=f"[ {combo_lbl} ]")
            self.label_footer_info.configure(text=self._t("cap_set", lbl=combo_lbl), text_color=COLORS["neon_cyan"])
            self._sync_ui_states()
            return

        others = {
            "hotkey_clicker": (self.hotkey_macro_record, self.hotkey_macro_play),
            "hotkey_macro_record": (self.hotkey_clicker, self.hotkey_macro_play),
            "hotkey_macro_play": (self.hotkey_clicker, self.hotkey_macro_record),
        }[mode]
        if any(same_key(key_obj, o) for o in others):
            self.label_hk_status.configure(text=self._t("hk_collision", lbl=label), text_color=COLORS["danger"])
            return

        if mode == "hotkey_clicker":
            self.hotkey_clicker, self.hotkey_clicker_label = key_obj, label
        elif mode == "hotkey_macro_record":
            self.hotkey_macro_record, self.hotkey_macro_record_label = key_obj, label
        elif mode == "hotkey_macro_play":
            self.hotkey_macro_play, self.hotkey_macro_play_label = key_obj, label

        self._refresh_hotkey_labels()
        self._sync_ui_states()
        self._persist_last_state()
        self.label_hk_status.configure(text=self._t("hk_updated", lbl=label), text_color=COLORS["neon_cyan"])

    # =========================================================================
    # Autoclicker Start / Stop
    # =========================================================================
    def toggle_clicking(self) -> None:
        if self.engine.running or self._pending_start_after is not None:
            self.stop_clicking()
        else:
            self.start_clicking(from_button=False)

    def start_clicking(self, from_button: bool = False) -> None:
        if self.engine.running or self._pending_start_after is not None:
            return
        if self.macro_recorder.recording or self.macro_player.running:
            self.stop_macro_all()

        hold_sec = self.card_hold.get_seconds()
        interval_sec = self.card_interval.get_seconds()
        is_inf = self.action_mode == "hold" and self.card_hold.infinite_var.get()

        if self.action_mode == "hold" and not is_inf and hold_sec < ClickerEngine.MIN_HOLD_SEC:
            hold_sec = 0.1
            self.card_hold.set_seconds(hold_sec)
        elif self.action_mode == "click" and interval_sec < ClickerEngine.MIN_INTERVAL_SEC:
            interval_sec = ClickerEngine.MIN_INTERVAL_SEC
            self.card_interval.set_seconds(interval_sec)

        try:
            max_cycles = max(1, int(self.entry_max_cycles.get().strip()))
        except ValueError:
            max_cycles = 100
        try:
            max_mins = max(0.05, float(self.entry_max_minutes.get().strip().replace(",", ".")))
        except ValueError:
            max_mins = 10.0

        cd_sec = 0.0
        if self.var_countdown.get():
            try:
                cd_sec = max(1.0, float(self.entry_countdown.get().strip().replace(",", ".")))
            except ValueError:
                cd_sec = 3.0

        target_win = ""
        if self.var_window_filter.get():
            sel = self.opt_window.get()
            if not self._is_window_placeholder(sel):
                target_win = sel

        live_zones = self._get_live_zones()
        for ov in self.zone_overlays:
            if ov.winfo_exists():
                ov.set_click_through(True)

        config = ClickerConfig(
            input_type=self.input_type,
            action_mode=self.action_mode,
            mouse_button=self.mouse_button,
            click_count=self.click_count,
            keyboard_key=self.target_keys[0],
            keyboard_keys=tuple(self.target_keys),
            hold_seconds=hold_sec,
            interval_seconds=interval_sec,
            infinite_hold=is_inf,
            cursor_mode=self.cursor_mode if self.input_type == "mouse" else "real",
            target_zones=live_zones,
            stop_limit_mode=self.stop_limit_mode,
            stop_max_cycles=max_cycles,
            stop_max_minutes=max_mins,
            countdown_seconds=cd_sec,
            target_window_title=target_win,
            failsafe_enabled=self.var_failsafe.get(),
        )

        if from_button and cd_sec <= 0 and self.input_type == "mouse" and self.mouse_button == Button.left:
            self.label_phase.configure(text=f"●  {self._t('phase_starting')}", text_color=COLORS["accent_sky"])
            self._pending_start_after = self.after(250, lambda: self._launch_engine(config))
            self._sync_ui_states()
        else:
            self._launch_engine(config)

    def _launch_engine(self, config: ClickerConfig) -> None:
        self._pending_start_after = None
        self.engine.start(config)
        play_tone("start", self.var_sound.get())
        self._sync_ui_states()
        self.label_footer_info.configure(text=self._t("footer_active", hk=self.hotkey_clicker_label), text_color=COLORS["neon_cyan"])

    def stop_clicking(self) -> None:
        was_active = self.engine.running or self._pending_start_after is not None
        if self._pending_start_after is not None:
            self.after_cancel(self._pending_start_after)
            self._pending_start_after = None
        self.engine.stop()
        for ov in self.zone_overlays:
            if ov.winfo_exists():
                ov.set_click_through(False)
        if was_active:
            play_tone("stop", self.var_sound.get())
        self._sync_ui_states()
        self.label_footer_info.configure(text=self._t("footer_stopped"), text_color=COLORS["text_muted"])

    def _on_clicker_auto_finished(self, reason: str) -> None:
        for ov in self.zone_overlays:
            if ov.winfo_exists():
                ov.set_click_through(False)
        self._sync_ui_states()
        if reason == "failsafe":
            play_tone("failsafe", self.var_sound.get())
            self.label_footer_info.configure(text=self._t("footer_failsafe"), text_color=COLORS["danger"])
        elif reason == "cycles":
            play_tone("finish", self.var_sound.get())
            self.label_footer_info.configure(text=self._t("footer_limit_cycles"), text_color=COLORS["holo_green"])
        elif reason == "time":
            play_tone("finish", self.var_sound.get())
            self.label_footer_info.configure(text=self._t("footer_limit_time"), text_color=COLORS["holo_green"])

    # =========================================================================
    # Macro Recording, Visual Editing, Library & Playback
    # =========================================================================
    def _refresh_macro_library(self) -> None:
        files = list_saved_macros()
        values = files if files else [self._t("no_macros")]
        current = self.opt_macro_lib.get()
        self.opt_macro_lib.configure(values=values)
        self.opt_macro_lib.set(current if current in values else values[0])

    def _on_macro_lib_select(self, filename: str) -> None:
        if filename in ("(Sin macros guardadas)", "(No saved macros)") or self.macro_recorder.recording or self.macro_player.running:
            return
        full_path = os.path.join(get_macros_dir(), filename)
        if not os.path.exists(full_path):
            return
        try:
            self.macro_events = load_macro_json(full_path)
            self._render_macro_list()
            self._sync_macro_ui()
            self.label_macro_footer.configure(text=self._t("macro_loaded_lib", name=filename), text_color=COLORS["neon_cyan"])
        except Exception as exc:
            self.label_macro_footer.configure(text=self._t("macro_err_open", err=exc), text_color=COLORS["danger"])

    def toggle_macro_recording(self, from_button: bool = False) -> None:
        if self.macro_recorder.recording:
            self._stop_macro_recording()
        else:
            self._start_macro_recording(from_button=from_button)

    def _start_macro_recording(self, from_button: bool = False) -> None:
        if self.macro_recorder.recording or self._pending_macro_after is not None:
            return
        if self.engine.running:
            self.stop_clicking()
        if self.macro_player.running:
            self.macro_player.stop()

        rec_kb = self.var_rec_keyboard.get()
        rec_mc = self.var_rec_mouse_clicks.get()
        rec_mm = self.var_rec_mouse_moves.get()
        if not (rec_kb or rec_mc or rec_mm):
            self.label_macro_footer.configure(text=self._t("macro_err_device"), text_color=COLORS["danger"])
            return

        def _begin() -> None:
            self._pending_macro_after = None
            excluded = [self.hotkey_clicker, self.hotkey_macro_record, self.hotkey_macro_play]
            self.macro_recorder.start(
                record_keyboard=rec_kb, record_mouse_clicks=rec_mc,
                record_mouse_moves=rec_mm, excluded_keys=excluded, attach_listeners=True,
            )
            play_tone("record", self.var_sound.get())
            self._sync_macro_ui()
            self.label_macro_footer.configure(
                text=self._t("macro_recording_msg", hk=self.hotkey_macro_record_label), text_color=COLORS["danger"],
            )

        if from_button:
            self._pending_macro_after = self.after(180, _begin)
        else:
            _begin()

    def _stop_macro_recording(self) -> None:
        if self._pending_macro_after is not None:
            self.after_cancel(self._pending_macro_after)
            self._pending_macro_after = None
        self.macro_events = self.macro_recorder.stop()
        play_tone("stop", self.var_sound.get())
        self._render_macro_list()
        self._sync_macro_ui()
        stats = summarize_events(self.macro_events)
        self.label_macro_footer.configure(
            text=self._t("macro_recorded_msg", n=stats["total"], dur=format_short_total(stats["duration"])),
            text_color=COLORS["neon_cyan"],
        )

    def toggle_macro_playback(self) -> None:
        if self.macro_player.running or self.macro_recorder.recording or self._pending_macro_after is not None:
            self.stop_macro_all()
        else:
            self.start_macro_playback(from_button=False)

    def start_macro_playback(self, from_button: bool = False) -> None:
        if self.macro_player.running or self.macro_recorder.recording or self._pending_macro_after is not None:
            return
        if not self.macro_events:
            self.label_macro_footer.configure(text=self._t("macro_need_events"), text_color=COLORS["danger"])
            return
        if self.engine.running:
            self.stop_clicking()

        try:
            repeats = max(1, int(self.entry_macro_repeats.get().strip()))
        except ValueError:
            repeats = 1
        try:
            raw_delay = self.entry_macro_delay.get().strip().lower().replace(",", ".")
            for suffix in ("seg", "sec", "s"):
                if raw_delay.endswith(suffix):
                    raw_delay = raw_delay[: -len(suffix)].strip()
                    break
            delay = max(0.0, float(raw_delay))
        except ValueError:
            delay = 0.5
        try:
            speed = float(self.seg_macro_speed.get().replace("x", ""))
        except ValueError:
            speed = 1.0

        cfg = MacroPlayConfig(speed=speed, repeat_count=repeats, infinite_loop=self.var_macro_infinite.get(), loop_delay=delay)

        def _launch() -> None:
            self._pending_macro_after = None
            self.macro_player.start(self.macro_events, cfg)
            play_tone("start", self.var_sound.get())
            self._sync_macro_ui()
            self.label_macro_footer.configure(
                text=self._t("macro_playing_msg", hk=self.hotkey_macro_play_label), text_color=COLORS["neon_cyan"],
            )

        if from_button:
            self._pending_macro_after = self.after(250, _launch)
        else:
            _launch()

    def stop_macro_all(self) -> None:
        was_playing = self.macro_player.running
        if self._pending_macro_after is not None:
            self.after_cancel(self._pending_macro_after)
            self._pending_macro_after = None
        if self.macro_recorder.recording:
            self._stop_macro_recording()
        if was_playing:
            self.macro_player.stop()
            play_tone("stop", self.var_sound.get())
        self._sync_macro_ui()
        self.label_macro_footer.configure(text=self._t("macro_stopped_msg"), text_color=COLORS["text_muted"])

    def _on_macro_playback_finished(self) -> None:
        play_tone("finish", self.var_sound.get())
        self._sync_macro_ui()
        self.label_macro_footer.configure(text=self._t("macro_finished_msg"), text_color=COLORS["holo_green"])

    def _get_selected_step_index(self) -> Optional[int]:
        sel = self.listbox_macro.curselection()
        if not sel:
            return None
        idx = int(sel[0])
        return idx if 0 <= idx < len(self.macro_events) else None

    def _parse_step_seconds(self) -> float:
        try:
            return max(0.0, float(self.entry_step_sec.get().strip().replace(",", ".")))
        except ValueError:
            return 0.5

    def _delete_selected_macro_step(self) -> None:
        if self.macro_recorder.recording or self.macro_player.running:
            return
        idx = self._get_selected_step_index()
        if idx is None:
            self.label_macro_footer.configure(text=self._t("macro_pick_del"), text_color=COLORS["warn_amber"])
            return
        self.macro_events = delete_event_at(self.macro_events, idx)
        self._render_macro_list(select_idx=min(idx, len(self.macro_events) - 1))
        self._sync_macro_ui()
        self.label_macro_footer.configure(text=self._t("macro_deleted_step", n=idx + 1), text_color=COLORS["neon_cyan"])

    def _insert_pause_at_selected(self) -> None:
        if self.macro_recorder.recording or self.macro_player.running:
            return
        sec = self._parse_step_seconds()
        idx = self._get_selected_step_index()
        if idx is None:
            idx = max(0, len(self.macro_events) - 1)
        self.macro_events = insert_pause_after(self.macro_events, idx, sec)
        self._render_macro_list(select_idx=min(idx + 1, len(self.macro_events) - 1))
        self._sync_macro_ui()
        self.label_macro_footer.configure(text=self._t("macro_pause_added", s=sec), text_color=COLORS["neon_cyan"])

    def _edit_selected_step_delay(self) -> None:
        if self.macro_recorder.recording or self.macro_player.running:
            return
        idx = self._get_selected_step_index()
        if idx is None or (idx == 0 and self.macro_events[0].kind != "pause"):
            self.label_macro_footer.configure(text=self._t("macro_pick_delay"), text_color=COLORS["warn_amber"])
            return
        sec = self._parse_step_seconds()
        self.macro_events = update_step_delay(self.macro_events, idx, sec)
        self._render_macro_list(select_idx=idx)
        self._sync_macro_ui()
        self.label_macro_footer.configure(text=self._t("macro_delay_set", n=idx + 1, s=sec), text_color=COLORS["neon_cyan"])

    def _clear_macro(self) -> None:
        if self.macro_recorder.recording or self.macro_player.running:
            return
        self.macro_events = []
        self._render_macro_list()
        self._sync_macro_ui()
        self.label_macro_footer.configure(text=self._t("macro_cleared"), text_color=COLORS["text_muted"])

    def _save_macro_dialog(self) -> None:
        if not self.macro_events:
            self.label_macro_footer.configure(text=self._t("macro_none_save"), text_color=COLORS["danger"])
            return
        path = filedialog.asksaveasfilename(
            title=self._t("dlg_save_macro"), initialdir=get_macros_dir(),
            defaultextension=".json", filetypes=[(self._t("dlg_macro_file"), "*.json")],
        )
        if not path:
            return
        try:
            save_macro_json(path, self.macro_events)
            self._refresh_macro_library()
            self.label_macro_footer.configure(text=self._t("macro_saved", name=os.path.basename(path)), text_color=COLORS["neon_cyan"])
        except Exception as exc:
            self.label_macro_footer.configure(text=self._t("macro_err_save", err=exc), text_color=COLORS["danger"])

    def _load_macro_dialog(self) -> None:
        if self.macro_recorder.recording or self.macro_player.running:
            return
        path = filedialog.askopenfilename(
            title=self._t("dlg_open_macro"), initialdir=get_macros_dir(),
            filetypes=[(self._t("dlg_macro_file"), "*.json")],
        )
        if not path:
            return
        try:
            self.macro_events = load_macro_json(path)
            self._render_macro_list()
            self._sync_macro_ui()
            self.label_macro_footer.configure(text=self._t("macro_loaded", name=os.path.basename(path)), text_color=COLORS["neon_cyan"])
        except Exception as exc:
            self.label_macro_footer.configure(text=self._t("macro_err_open", err=exc), text_color=COLORS["danger"])

    def _render_macro_list(self, select_idx: int = -1) -> None:
        stats = summarize_events(self.macro_events)
        self.badge_macro_summary.configure(
            text=self._t("macro_summary_fmt", total=stats["total"], keys=stats["keys"], clicks=stats["clicks"], dur=stats["duration"])
        )
        self.listbox_macro.delete(0, tk.END)
        if not self.macro_events:
            self.listbox_macro.insert(tk.END, self._t("macro_empty_1"))
            self.listbox_macro.insert(tk.END, self._t("macro_empty_2", hk=self.hotkey_macro_record_label))
        else:
            for i, ev in enumerate(self.macro_events[:500]):
                self.listbox_macro.insert(tk.END, " " + ev.format_line(index=i + 1))
            if len(self.macro_events) > 500:
                self.listbox_macro.insert(tk.END, self._t("macro_more", n=len(self.macro_events) - 500))
            if 0 <= select_idx < min(len(self.macro_events), 500):
                self.listbox_macro.selection_set(select_idx)
                self.listbox_macro.see(select_idx)

    def _sync_macro_ui(self) -> None:
        rec = self.macro_recorder.recording
        playing = self.macro_player.running
        busy = rec or playing
        has_ev = len(self.macro_events) > 0
        is_inf = self.var_macro_infinite.get()

        st = "disabled" if busy else "normal"
        for w in (self.chk_rec_kb, self.chk_rec_mouse, self.chk_rec_moves, self.seg_macro_speed, self.entry_macro_delay, self.opt_macro_lib, self.btn_macro_load):
            w.configure(state=st)
        self.switch_macro_inf.configure(
            state=st,
            button_color=COLORS["neon_cyan"] if (not busy and is_inf) else ("#6EA4D4" if not busy else COLORS["text_muted"]),
        )
        self.entry_macro_repeats.configure(state="disabled" if (busy or is_inf) else "normal")
        for b in (self.btn_macro_save, self.btn_macro_clear, self.btn_step_delete, self.btn_step_delay):
            b.configure(state="normal" if (has_ev and not busy) else "disabled")
        self.btn_step_pause.configure(state="normal" if not busy else "disabled")

        if rec:
            self.btn_macro_record.configure(
                state="normal", text=self._t("macro_stop_rec_btn", hk=self.hotkey_macro_record_label),
                fg_color=COLORS["danger_bg"], border_color=COLORS["danger"], text_color="#FF6B8E",
            )
        else:
            self.btn_macro_record.configure(
                state="disabled" if playing else "normal",
                text=self._t("macro_rec_btn", hk=self.hotkey_macro_record_label),
                fg_color=COLORS["neon_dim"], border_color=COLORS["neon_cyan"] if not playing else COLORS["border"],
                text_color=COLORS["neon_cyan"] if not playing else COLORS["text_muted"],
            )

        if playing:
            self.btn_macro_play.configure(state="disabled", fg_color="#091C36", border_color=COLORS["border"], text_color=COLORS["text_muted"])
            self.btn_macro_stop.configure(state="normal", fg_color=COLORS["danger_bg"], border_color=COLORS["danger"], text_color="#FF6B8E")
        else:
            self.btn_macro_play.configure(
                state="normal" if (has_ev and not rec) else "disabled",
                fg_color=COLORS["neon_blue"] if (has_ev and not rec) else "#091C36",
                border_color=COLORS["neon_cyan"] if (has_ev and not rec) else COLORS["border"],
                text_color="#FFFFFF" if (has_ev and not rec) else COLORS["text_muted"],
            )
            self.btn_macro_stop.configure(
                state="normal" if rec else "disabled",
                fg_color=COLORS["danger_bg"] if rec else "#10080D",
                border_color=COLORS["danger"] if rec else COLORS["danger_dim_border"],
                text_color="#FF6B8E" if rec else COLORS["text_muted"],
            )

    # =========================================================================
    # Live HUD Refresh Loop (~33 FPS on main UI thread)
    # =========================================================================
    def _schedule_hud_update(self) -> None:
        self._update_all_huds()
        self._hud_after_id = self.after(30, self._schedule_hud_update)

    def _update_all_huds(self) -> None:
        snap = self.engine.get_snapshot()
        is_rec, rec_count, rec_elapsed = self.macro_recorder.get_live_stats()
        msnap = self.macro_player.get_snapshot()

        mini_phase = self._t("phase_stopped")
        mini_time = "00:00.00"
        mini_active = False
        mini_accent = COLORS["text_muted"]
        mini_counter = ""

        if not snap.running:
            if self._pending_start_after is None:
                self.hud_card.configure(border_color=COLORS["border"])
                self.label_phase.configure(text=f"●  {self._t('phase_stopped')}", text_color=COLORS["text_muted"])
                self.label_cycles.configure(text=self._t("cycles_fmt", n=snap.cycle_count))
                self.label_clock.configure(text="00:00.00", text_color=COLORS["text_muted"])
                self.label_clock_target.configure(text_color=COLORS["text_muted"])
                self.progress_bar.set(0.0)
                self.progress_bar.configure(progress_color="#0A192C")
        else:
            mini_active = True
            self.label_cycles.configure(text=self._t("cycles_fmt", n=snap.cycle_count))
            if self.stop_limit_mode == "cycles":
                mini_counter = f"{snap.cycle_count}/{self.entry_max_cycles.get().strip() or '?'}"
            else:
                mini_counter = f"✓ {snap.cycle_count}"

            loc_phase = translate_engine_phase(self.lang, snap.phase)

            if snap.phase == "CUENTA REGRESIVA":
                rem = max(0.0, snap.phase_total - snap.phase_elapsed)
                self.hud_card.configure(border_color=COLORS["warn_amber"])
                self.label_phase.configure(text=f"●  {loc_phase}", text_color=COLORS["warn_amber"])
                self.label_clock.configure(text=format_timer(rem), text_color=COLORS["warn_amber"])
                self.label_clock_target.configure(text=f"/ {format_timer(snap.phase_total)}", text_color=COLORS["text_muted"])
                self.progress_bar.configure(progress_color=COLORS["warn_amber"])
                self.progress_bar.set(rem / snap.phase_total if snap.phase_total > 0 else 0.0)
                mini_phase, mini_time, mini_accent = self._t("mini_starting"), format_timer(rem), COLORS["warn_amber"]

            elif snap.phase == "PAUSADO (VENTANA)":
                self.hud_card.configure(border_color=COLORS["warn_amber"])
                self.label_phase.configure(text=f"●  {loc_phase}", text_color=COLORS["warn_amber"])
                self.label_clock.configure(text=self._t("pause_word"), text_color=COLORS["warn_amber"])
                self.progress_bar.configure(progress_color=COLORS["warn_amber"])
                self.progress_bar.set(0.0)
                mini_phase, mini_time, mini_accent = self._t("mini_paused_app"), self._t("pause_word"), COLORS["warn_amber"]

            elif snap.phase.startswith("MANTENIENDO"):
                self.hud_card.configure(border_color=COLORS["neon_cyan"])
                self.label_phase.configure(text=f"●  {loc_phase}", text_color=COLORS["neon_cyan"])
                t_str = format_timer(snap.phase_elapsed)
                self.label_clock.configure(text=t_str, text_color=COLORS["neon_cyan"])
                self.progress_bar.configure(progress_color=COLORS["neon_cyan"])
                if snap.infinite:
                    self.label_clock_target.configure(text="/ ∞", text_color=COLORS["accent_sky"])
                    pulse = 0.5 + 0.5 * (1.0 - abs((snap.phase_elapsed % 2.0) - 1.0))
                    self.progress_bar.set(0.25 + 0.75 * pulse)
                else:
                    self.label_clock_target.configure(text=f"/ {format_timer(snap.phase_total)}", text_color=COLORS["text_secondary"])
                    self.progress_bar.set(min(1.0, snap.phase_elapsed / snap.phase_total) if snap.phase_total > 0 else 1.0)
                mini_phase, mini_time, mini_accent = loc_phase, t_str, COLORS["neon_cyan"]

            elif snap.phase == "ESPERANDO":
                rem = max(0.0, snap.phase_total - snap.phase_elapsed)
                t_str = format_timer(rem)
                self.hud_card.configure(border_color=COLORS["border_blue"])
                self.label_phase.configure(text=f"●  {loc_phase}", text_color=COLORS["accent_sky"])
                self.label_clock.configure(text=t_str, text_color=COLORS["accent_sky"])
                self.label_clock_target.configure(text=f"/ {format_timer(snap.phase_total)}", text_color=COLORS["text_muted"])
                self.progress_bar.configure(progress_color=COLORS["neon_blue"])
                self.progress_bar.set(min(1.0, rem / snap.phase_total) if snap.phase_total > 0 else 0.0)
                mini_phase, mini_time, mini_accent = self._t("mini_waiting"), t_str, COLORS["accent_sky"]

            else:
                t_str = format_timer(snap.phase_elapsed)
                self.hud_card.configure(border_color=COLORS["neon_cyan"])
                self.label_phase.configure(text=f"●  {loc_phase}", text_color=COLORS["neon_cyan"])
                self.label_clock.configure(text=t_str, text_color=COLORS["neon_cyan"])
                self.progress_bar.configure(progress_color=COLORS["neon_cyan"])
                self.progress_bar.set(1.0)
                mini_phase, mini_time, mini_accent = loc_phase, t_str, COLORS["neon_cyan"]

        # --- Macro HUD ---
        if is_rec:
            self.macro_hud_card.configure(border_color=COLORS["danger"])
            self.label_macro_phase.configure(text=f"●  {self._t('phase_recording')}", text_color=COLORS["danger"])
            self.label_macro_clock.configure(text=f"{format_timer(rec_elapsed)} / REC", text_color="#FF6B8E")
            self.label_macro_counters.configure(text=self._t("captured_fmt", n=rec_count))
            pulse = 0.5 + 0.5 * (1.0 - abs((rec_elapsed % 1.4) - 0.7) / 0.7)
            self.macro_progress.configure(progress_color=COLORS["danger"])
            self.macro_progress.set(0.2 + 0.8 * pulse)
            mini_phase, mini_time, mini_active, mini_accent = self._t("mini_recording"), format_timer(rec_elapsed), True, COLORS["danger"]
            mini_counter = f"{rec_count} ev"
        elif msnap.running:
            loc_mphase = translate_engine_phase(self.lang, msnap.phase)
            is_loop_pause = msnap.phase == "PAUSA ENTRE CICLOS"
            m_accent = COLORS["accent_sky"] if is_loop_pause else COLORS["neon_cyan"]
            self.macro_hud_card.configure(border_color=COLORS["border_blue"] if is_loop_pause else COLORS["neon_cyan"])
            self.label_macro_phase.configure(text=f"●  {loc_mphase}", text_color=m_accent)
            self.label_macro_clock.configure(
                text=f"{format_timer(msnap.elapsed)} / {format_timer(msnap.total_duration)}",
                text_color=m_accent,
            )
            if msnap.infinite:
                l_txt = f"✓ {msnap.completed_loops} (∞)"
                mini_counter = f"✓ {msnap.completed_loops} (∞)"
            else:
                l_txt = f"{msnap.current_loop}/{msnap.total_loops}"
                mini_counter = f"{msnap.current_loop}/{msnap.total_loops}"
            self.label_macro_counters.configure(text=self._t("step_fmt", i=msnap.event_index, t=msnap.total_events, l=l_txt))
            self.macro_progress.configure(progress_color=COLORS["neon_blue"] if is_loop_pause else COLORS["neon_cyan"])
            self.macro_progress.set(min(1.0, msnap.elapsed / msnap.total_duration) if msnap.total_duration > 0 else 1.0)
            mini_phase = self._t("mini_waiting") if is_loop_pause else self._t("mini_macro")
            mini_time, mini_active, mini_accent = format_timer(msnap.elapsed), True, m_accent
        else:
            stats = summarize_events(self.macro_events)
            self.macro_hud_card.configure(border_color=COLORS["border"])
            self.label_macro_phase.configure(text=f"●  {self._t('phase_ready')}", text_color=COLORS["text_muted"])
            self.label_macro_clock.configure(text=f"00:00.00 / {format_timer(stats['duration'])}", text_color=COLORS["text_muted"])
            self.label_macro_counters.configure(text=self._t("events_fmt", n=stats["total"]))
            self.macro_progress.set(0.0)
            self.macro_progress.configure(progress_color="#0A192C")
            if not mini_active and self.active_page == "macros" and self.macro_events:
                if self.var_macro_infinite.get():
                    mini_counter = f"✓ {msnap.completed_loops} (∞)"
                else:
                    rep = self.entry_macro_repeats.get().strip() or "1"
                    mini_counter = f"{msnap.completed_loops}/{rep}"

        self.label_sb_status.configure(text=f"●  {mini_phase}", text_color=mini_accent)
        self.sidebar_status_card.configure(border_color=mini_accent if mini_active else COLORS["border"])
        if self.mini_hud is not None and self.mini_hud.winfo_exists():
            self.mini_hud.update_state(mini_phase, mini_time, mini_active, mini_accent, counter_text=mini_counter)

        if self.reticle_color == "rainbow" and self.zones_visible and self.zone_overlays:
            self._apply_reticle_colors_now()

    def on_close(self) -> None:
        self._persist_last_state()
        for aid in (self._hud_after_id, self._pending_start_after, self._pending_macro_after):
            if aid is not None:
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
        self.engine.stop()
        self.macro_recorder.stop()
        self.macro_player.stop()
        self._destroy_zone_overlays()
        if self.mini_hud is not None and self.mini_hud.winfo_exists():
            try:
                self.mini_hud.destroy()
            except Exception:
                pass
        try:
            self.listener.stop()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = AutoclickerApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
