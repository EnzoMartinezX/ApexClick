import colorsys
import ctypes
from ctypes import wintypes
import tkinter as tk
from typing import Callable, Dict, List, Optional, Tuple

import customtkinter as ctk


GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020


# Built-in neon reticle presets: (main_hex, glow_hex, display_label)
RETICLE_PRESETS: Dict[str, Tuple[str, str, str]] = {
    "green": ("#39FF14", "#167A08", "Verde"),
    "red": ("#FF2A4B", "#7A0D1E", "Rojo"),
    "cyan": ("#00F0FF", "#086A7A", "Celeste"),
    "pink": ("#FF3ED6", "#7A1264", "Rosado"),
    "rainbow": ("#39FF14", "#167A08", "Arcoíris"),
}


def hex_to_glow(hex_color: str) -> str:
    """Derive a darker holographic glow hex from any #RRGGBB color (never #010203)."""
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        return "#167A08"
    try:
        r = max(12, int(int(cleaned[0:2], 16) * 0.45))
        g = max(12, int(int(cleaned[2:4], 16) * 0.45))
        b = max(12, int(int(cleaned[4:6], 16) * 0.45))
        return f"#{r:02X}{g:02X}{b:02X}"
    except ValueError:
        return "#167A08"


def resolve_reticle_colors(color_spec: str, phase: float = 0.0) -> Tuple[str, str]:
    """Return (main_hex, glow_hex) for a preset name, 'rainbow', or custom #RRGGBB."""
    if color_spec == "rainbow":
        hue = phase % 1.0
        r1, g1, b1 = colorsys.hsv_to_rgb(hue, 0.92, 1.0)
        r2, g2, b2 = colorsys.hsv_to_rgb(hue, 0.92, 0.46)
        main = f"#{int(r1 * 255):02X}{int(g1 * 255):02X}{int(b1 * 255):02X}"
        glow = f"#{max(12, int(r2 * 255)):02X}{max(12, int(g2 * 255)):02X}{max(12, int(b2 * 255)):02X}"
        return main, glow
    if color_spec in RETICLE_PRESETS:
        main, glow, _ = RETICLE_PRESETS[color_spec]
        return main, glow
    if color_spec.startswith("#") and len(color_spec) == 7:
        return color_spec.upper(), hex_to_glow(color_spec)
    return "#39FF14", "#167A08"


class TargetMarkerWindow(tk.Toplevel):
    """Compact transparent-background holographic diamond reticle with live color & rainbow support."""

    SIZE = 46
    HALF = SIZE // 2
    TRANSPARENT_KEY = "#010203"

    def __init__(
        self,
        master: tk.Misc,
        initial_pos: Optional[Tuple[int, int]] = None,
        index_label: Optional[int] = None,
        main_color: str = "#39FF14",
        glow_color: str = "#167A08",
        on_move: Optional[Callable[[int, int], None]] = None,
    ):
        super().__init__(master)
        self._on_move = on_move
        self._index_label = index_label
        self._main_color = main_color
        self._glow_color = glow_color
        self._click_through = False
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        self._glow_poly_id: Optional[int] = None
        self._glow_line_ids: List[int] = []
        self._core_poly_id: Optional[int] = None
        self._core_line_ids: List[int] = []
        self._dot_id: Optional[int] = None
        self._text_id: Optional[int] = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self.TRANSPARENT_KEY)
        try:
            self.attributes("-transparentcolor", self.TRANSPARENT_KEY)
        except tk.TclError:
            pass

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        cx, cy = initial_pos if initial_pos is not None else (sw // 2, sh // 2)
        self._center_x = int(cx)
        self._center_y = int(cy)
        self.geometry(f"{self.SIZE}x{self.SIZE}+{self._center_x - self.HALF}+{self._center_y - self.HALF}")

        self.canvas = tk.Canvas(
            self,
            width=self.SIZE,
            height=self.SIZE,
            bg=self.TRANSPARENT_KEY,
            highlightthickness=0,
            cursor="fleur",
        )
        self.canvas.pack(fill="both", expand=True)
        self._draw_reticle()

        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)

    def set_index_label(self, index_label: Optional[int]) -> None:
        self._index_label = index_label
        self._draw_reticle()

    def set_color(self, main_color: str, glow_color: str) -> None:
        """Update diamond, ticks, center dot, and index number color in-place (0 flicker)."""
        if main_color == self._main_color and glow_color == self._glow_color:
            return
        self._main_color = main_color
        self._glow_color = glow_color
        if self._glow_poly_id is not None:
            self.canvas.itemconfig(self._glow_poly_id, outline=glow_color)
        for item_id in self._glow_line_ids:
            self.canvas.itemconfig(item_id, fill=glow_color)
        if self._core_poly_id is not None:
            self.canvas.itemconfig(self._core_poly_id, outline=main_color)
        for item_id in self._core_line_ids:
            self.canvas.itemconfig(item_id, fill=main_color)
        if self._dot_id is not None:
            self.canvas.itemconfig(self._dot_id, fill=main_color, outline=main_color)
        if self._text_id is not None:
            self.canvas.itemconfig(self._text_id, fill=main_color)

    def _draw_reticle(self) -> None:
        self.canvas.delete("all")
        self._glow_line_ids = []
        self._core_line_ids = []
        c = self.HALF          # 23
        r = 15                 # Compact 30px diamond matching user's SVG
        tick = 5               # Inward tick length
        glow_col = self._glow_color
        main_col = self._main_color

        pts = [c, c - r, c + r, c, c, c + r, c - r, c]

        # Subtle holographic glow stroke behind crisp core (zero background fill!)
        self._glow_poly_id = self.canvas.create_polygon(pts, outline=glow_col, width=3, fill="")
        self._glow_line_ids.append(self.canvas.create_line(c, c - r, c, c - r + tick, fill=glow_col, width=3))
        self._glow_line_ids.append(self.canvas.create_line(c, c + r - tick, c, c + r, fill=glow_col, width=3))
        self._glow_line_ids.append(self.canvas.create_line(c - r, c, c - r + tick, c, fill=glow_col, width=3))
        self._glow_line_ids.append(self.canvas.create_line(c + r - tick, c, c + r, c, fill=glow_col, width=3))

        # Crisp core diamond + 4 inward ticks + pinpoint center dot
        self._core_poly_id = self.canvas.create_polygon(pts, outline=main_col, width=1.6, fill="")
        self._core_line_ids.append(self.canvas.create_line(c, c - r, c, c - r + tick, fill=main_col, width=1.6))
        self._core_line_ids.append(self.canvas.create_line(c, c + r - tick, c, c + r, fill=main_col, width=1.6))
        self._core_line_ids.append(self.canvas.create_line(c - r, c, c - r + tick, c, fill=main_col, width=1.6))
        self._core_line_ids.append(self.canvas.create_line(c + r - tick, c, c + r, c, fill=main_col, width=1.6))
        self._dot_id = self.canvas.create_oval(c - 1, c - 1, c + 1, c + 1, fill=main_col, outline=main_col)

        # Compact floating zone index number (colored identically to reticle!)
        self._text_id = None
        if self._index_label is not None:
            self._text_id = self.canvas.create_text(
                self.SIZE - 6,
                7,
                text=str(self._index_label),
                fill=main_col,
                font=("Consolas", 8, "bold"),
            )

    def _on_drag_start(self, event: tk.Event) -> None:
        if self._click_through:
            return
        self._drag_offset_x = event.x
        self._drag_offset_y = event.y

    def _on_drag_motion(self, event: tk.Event) -> None:
        if self._click_through:
            return
        new_x = self.winfo_pointerx() - self._drag_offset_x
        new_y = self.winfo_pointery() - self._drag_offset_y
        self.geometry(f"+{new_x}+{new_y}")
        self._center_x = new_x + self.HALF
        self._center_y = new_y + self.HALF
        if self._on_move is not None:
            self._on_move(self._center_x, self._center_y)

    def _on_drag_end(self, _event: tk.Event) -> None:
        cx, cy = self.get_center_pos()
        self._center_x, self._center_y = cx, cy
        if self._on_move is not None:
            self._on_move(cx, cy)

    def _get_hwnd(self) -> int:
        # GA_ROOT = 2 walks only true parent windows, never crossing into the owner window
        hwnd = self.winfo_id()
        root = ctypes.windll.user32.GetAncestor(hwnd, 2)
        return root if root else hwnd

    def get_center_pos(self) -> Tuple[int, int]:
        try:
            rect = wintypes.RECT()
            hwnd = self._get_hwnd()
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                if cx != 0 or cy != 0:
                    self._center_x, self._center_y = cx, cy
                    return (cx, cy)
        except Exception:
            pass
        return (self._center_x, self._center_y)

    def set_center_pos(self, x: int, y: int) -> None:
        self._center_x = int(x)
        self._center_y = int(y)
        self.geometry(f"{self.SIZE}x{self.SIZE}+{self._center_x - self.HALF}+{self._center_y - self.HALF}")
        if self._on_move is not None:
            self._on_move(self._center_x, self._center_y)

    def set_click_through(self, enabled: bool) -> None:
        self._click_through = enabled
        self.canvas.configure(cursor="" if enabled else "fleur")
        try:
            self.update_idletasks()
            hwnd = self._get_hwnd()
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if enabled:
                new_style = ex_style | WS_EX_LAYERED | WS_EX_TRANSPARENT
            else:
                new_style = (ex_style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
        except Exception:
            pass


class MiniHudWindow(ctk.CTkToplevel):
    """Compact draggable floating obsidian-neon mini HUD bar with live counters."""

    def __init__(
        self,
        master: tk.Misc,
        on_toggle: Callable[[], None],
        on_restore: Callable[[], None],
    ):
        super().__init__(master)
        self._on_toggle = on_toggle
        self._on_restore = on_restore
        self._drag_x = 0
        self._drag_y = 0

        # Build hidden at alpha=0.0 so Mini-HUD opens without any white flash
        self.attributes("-alpha", 0.0)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color="#04070E")
        self.geometry("300x56+40+40")

        self.border = ctk.CTkFrame(
            self,
            fg_color="#060B15",
            corner_radius=12,
            border_width=2,
            border_color="#0080FF",
        )
        self.border.pack(fill="both", expand=True)

        left = ctk.CTkFrame(self.border, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=5)

        self.label_phase = ctk.CTkLabel(
            left,
            text="● DETENIDO",
            font=("Segoe UI", 9, "bold"),
            text_color="#476582",
            anchor="w",
        )
        self.label_phase.pack(anchor="w")

        self.label_time = ctk.CTkLabel(
            left,
            text="00:00.00",
            font=("Consolas", 15, "bold"),
            text_color="#EAF6FF",
            anchor="w",
        )
        self.label_time.pack(anchor="w")

        self.label_counter = ctk.CTkLabel(
            self.border,
            text="",
            font=("Consolas", 11, "bold"),
            text_color="#00F0FF",
            fg_color="#0B203B",
            corner_radius=7,
            height=28,
            padx=8,
        )
        self._counter_visible = False

        self.btn_toggle = ctk.CTkButton(
            self.border,
            text="▶",
            width=34,
            height=32,
            corner_radius=8,
            font=("Consolas", 14, "bold"),
            fg_color="#007BFF",
            hover_color="#00A2FF",
            border_width=1,
            border_color="#00F0FF",
            text_color="#FFFFFF",
            command=self._on_toggle,
        )
        self.btn_toggle.pack(side="left", padx=(4, 4))

        self.btn_restore = ctk.CTkButton(
            self.border,
            text="↗",
            width=30,
            height=32,
            corner_radius=8,
            font=("Segoe UI", 12, "bold"),
            fg_color="#0B203B",
            hover_color="#12365E",
            border_width=1,
            border_color="#132E52",
            text_color="#00F0FF",
            command=self._on_restore,
        )
        self.btn_restore.pack(side="left", padx=(0, 8))

        for widget in (self.border, left, self.label_phase, self.label_counter, self.label_time):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        self.update_idletasks()
        self.update()
        self.after(25, self._reveal)

    def _reveal(self) -> None:
        try:
            if self.winfo_exists():
                self.attributes("-alpha", 1.0)
        except Exception:
            pass

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _on_drag(self, event: tk.Event) -> None:
        nx = event.x_root - self._drag_x
        ny = event.y_root - self._drag_y
        self.geometry(f"+{nx}+{ny}")

    def update_state(
        self,
        phase: str,
        time_text: str,
        active: bool,
        accent: str,
        counter_text: str = "",
    ) -> None:
        self.label_phase.configure(text=f"● {phase}", text_color=accent)
        self.label_time.configure(text=time_text, text_color=accent if active else "#7CA8D1")
        self.border.configure(border_color=accent if active else "#132E52")

        if counter_text:
            self.label_counter.configure(text=counter_text, text_color=accent if active else "#7CA8D1")
            if not self._counter_visible:
                self.label_counter.pack(side="left", before=self.btn_toggle, padx=(2, 4))
                self._counter_visible = True
        else:
            if self._counter_visible:
                self.label_counter.pack_forget()
                self._counter_visible = False

        if active:
            self.btn_toggle.configure(
                text="■",
                fg_color="#1C0813",
                hover_color="#310E21",
                border_color="#FF2E63",
                text_color="#FF6B8E",
            )
        else:
            self.btn_toggle.configure(
                text="▶",
                fg_color="#007BFF",
                hover_color="#00A2FF",
                border_color="#00F0FF",
                text_color="#FFFFFF",
            )
