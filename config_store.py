import json
import os
import sys
from typing import Any, Dict, List


def get_app_dir() -> str:
    """Return persistent directory next to the .exe (frozen) or script (dev)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_config_path() -> str:
    return os.path.join(get_app_dir(), "config.json")


def get_macros_dir() -> str:
    path = os.path.join(get_app_dir(), "macros")
    os.makedirs(path, exist_ok=True)
    return path


DEFAULT_STATE: Dict[str, Any] = {
    "language": "es",
    "input_type": "mouse",
    "action_mode": "hold",
    "mouse_button": "left",
    "click_count": 1,
    "target_keys": [{"type": "special", "name": "space", "label": "ESPACIO"}],
    "hold_seconds": 1.5,
    "interval_seconds": 1.0,
    "infinite_hold": False,
    "cursor_mode": "real",
    "zones": [[960, 540]],
    "reticle_color": "green",
    "reticle_show_numbers": True,
    "stop_limit_mode": "none",
    "stop_max_cycles": 100,
    "stop_max_minutes": 10.0,
    "countdown_enabled": False,
    "countdown_seconds": 3,
    "window_filter_enabled": False,
    "target_window_title": "",
    "failsafe_enabled": True,
    "sound_enabled": True,
    "topmost": False,
    "hotkey_clicker": {"type": "special", "name": "f6", "label": "F6"},
    "hotkey_macro_record": {"type": "special", "name": "f7", "label": "F7"},
    "hotkey_macro_play": {"type": "special", "name": "f8", "label": "F8"},
}


def load_config_file(path: str | None = None) -> Dict[str, Any]:
    cfg_path = path or get_config_path()
    base = {"version": 2, "last_state": dict(DEFAULT_STATE), "profiles": {}}
    if not os.path.exists(cfg_path):
        return base
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            return base
        merged_state = dict(DEFAULT_STATE)
        if isinstance(raw.get("last_state"), dict):
            merged_state.update(raw["last_state"])
        profiles = raw.get("profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
        return {"version": 2, "last_state": merged_state, "profiles": profiles}
    except Exception:
        return base


def save_config_file(data: Dict[str, Any], path: str | None = None) -> None:
    cfg_path = path or get_config_path()
    payload = {
        "version": 2,
        "last_state": data.get("last_state", dict(DEFAULT_STATE)),
        "profiles": data.get("profiles", {}),
    }
    tmp_path = cfg_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp_path, cfg_path)


def list_saved_macros(macros_dir: str | None = None) -> List[str]:
    folder = macros_dir or get_macros_dir()
    if not os.path.isdir(folder):
        return []
    files = [f for f in os.listdir(folder) if f.lower().endswith(".json")]
    files.sort(key=str.lower)
    return files
