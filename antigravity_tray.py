#!/usr/bin/env python3
"""
Antigravity Quota Tray Monitor (OmniRoute & 9Router)
Monochrome Windows 11 system tray monitor for Antigravity account quotas.
Supports OmniRoute (remote/server) and 9Router (local) with full GUI Settings dialog.
Tracks aggregated pool percentage, individual accounts, and quota reset times.
"""

import ctypes
import sys

# Strip inherited external virtualenvs from sys.path to prevent C-extension binary collisions
sys.path = [p for p in sys.path if not ("environments" in p and "venv" in p)]

# 1. Enable Per-Monitor V2 DPI awareness BEFORE importing any UI or graphics libs
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

import concurrent.futures
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFont
import pystray
import pystray._win32

# Enable dark mode for Win32 menus in Windows 10/11
try:
    _uxtheme = ctypes.windll.uxtheme
    _uxtheme[135](2)  # SetPreferredAppMode(ForceDark)
    _uxtheme[136]()   # FlushMenuThemes()

    _orig_create_window = pystray._win32.Icon._create_window

    def _dark_create_window(self, atom):
        hwnd = _orig_create_window(self, atom)
        try:
            _uxtheme[133](hwnd, True)  # AllowDarkModeForWindow
            _uxtheme.SetWindowTheme(hwnd, "DarkMode_Explorer", None)
            _uxtheme[136]()            # FlushMenuThemes
        except Exception:
            pass
        return hwnd

    pystray._win32.Icon._create_window = _dark_create_window

    _orig_on_notify = pystray._win32.Icon._on_notify

    def _custom_on_notify(self, wparam, lparam):
        # 0x0202 = WM_LBUTTONUP, 0x0203 = WM_LBUTTONDBLCLK
        if lparam in (pystray._win32.win32.WM_LBUTTONUP, 0x0203):
            self()
        else:
            _orig_on_notify(self, wparam, lparam)

    pystray._win32.Icon._on_notify = _custom_on_notify
except Exception as _e:
    logging.warning("Не удалось настроить окно меню: %s", _e)

LOG_DIR = Path.home() / ".hermes"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "antigravity_tray.log"
CONFIG_FILE = LOG_DIR / "antigravity_tray_config.json"

logging.basicConfig(
    handlers=[RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")],
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# In-memory font cache to avoid parsing TTF file on disk repeatedly
FONT_CACHE = {}

def get_font(size: int):
    if size not in FONT_CACHE:
        try:
            FONT_CACHE[size] = ImageFont.truetype("segoeuib.ttf", size)
        except Exception:
            FONT_CACHE[size] = ImageFont.load_default()
    return FONT_CACHE[size]

DEFAULT_CONFIG = {
    "server_type": "omniroute",          # "omniroute" or "9router"
    "preset": "omniroute_server",        # "omniroute_server", "9router_local", "omniroute_local", "custom"
    "server_url": "https://omni.cub0d.dev",
    "api_key": "sk-omni-master-1235",
    "icon_style": "logo",               # "logo", "omni", "digits", "combo"
    "theme": "dark",                   # "dark", "light"
    "show_gemini": True,
    "show_gemini_weekly": True,
    "show_claude": True,
    "show_claude_weekly": True,
    "show_accounts_detail": True,
    "timer_format": "minutes",         # "minutes" (135 мин) or "hours" (2ч 15м)
    "show_timer": False,
    "poll_interval": 60,               # seconds: 30, 60, 120, 300
    "main_model": "gemini"             # "gemini", "claude", "avg"
}

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0.0.0 Safari/537.36"
}

def load_config():
    cfg = DEFAULT_CONFIG.copy()
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg.update(data)
    except Exception as e:
        logging.warning("Ошибка чтения конфигурации: %s", e)
    return cfg

def save_config(cfg):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.warning("Ошибка сохранения конфигурации: %s", e)

# ----------------- 9Router Local Providers -----------------

def get_9router_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        p = Path(appdata) / "9router"
        if p.exists():
            return p
    home_p = Path.home() / ".9router"
    if home_p.exists():
        return home_p
    raise FileNotFoundError("Директория 9router не найдена.")

def get_cli_token(base_dir: Path) -> str:
    mid = (base_dir / "machine-id").read_text(encoding="utf-8").strip()
    sec = (base_dir / "auth" / "cli-secret").read_text(encoding="utf-8").strip()
    return hashlib.sha256((mid + "9r-cli-auth" + sec).encode("utf-8")).hexdigest()[:16]

def get_accounts_9router(db_path: Path):
    if not db_path or not db_path.exists():
        raise FileNotFoundError(f"Файл базы {db_path} не найден")
    with sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True, timeout=10) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name FROM providerConnections WHERE provider='antigravity' AND isActive=1 ORDER BY createdAt ASC;")
        return c.fetchall()

def fetch_quota_9router(connection_id: str, token: str, port: int = 20128):
    url = f"http://localhost:{port}/api/usage/{connection_id}"
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    req.add_header("x-9r-cli-token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

# ----------------- OmniRoute Remote / Local Providers -----------------

def get_accounts_omniroute(server_url: str, api_key: str):
    url = f"{server_url.rstrip('/')}/api/providers?provider=antigravity"
    headers = {**HTTP_HEADERS}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        conns = data.get("connections", [])
        conns.sort(key=lambda x: (x.get("priority", 999), x.get("createdAt", "")))
        return [(c["id"], c.get("name") or c.get("email") or c["id"][:8]) for c in conns if c.get("isActive", True)]

def fetch_quota_omniroute(server_url: str, api_key: str, connection_id: str):
    url = f"{server_url.rstrip('/')}/api/usage/{connection_id}"
    headers = {**HTTP_HEADERS}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

# ----------------- Utility & Formatting -----------------

def parse_iso_time(iso_str):
    if not iso_str:
        return None
    try:
        clean_str = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def format_time_until(dt, fmt="minutes"):
    if not dt:
        return ""
    try:
        now = datetime.now(timezone.utc)
        delta = dt - now
        total_seconds = int(delta.total_seconds())
        if total_seconds <= 0:
            return "0 мин" if fmt == "minutes" else "сейчас"
        minutes = max(1, total_seconds // 60)
        if minutes >= 1440:
            d, rem = divmod(minutes, 1440)
            h = rem // 60
            return f"{d}д {h}ч"
        if fmt == "hours" and minutes >= 60:
            h, m = divmod(minutes, 60)
            return f"{h}ч {m}м"
        return f"{minutes} мин"
    except Exception:
        return ""

def format_short_reset(dt, fmt="minutes"):
    if not dt:
        return ""
    try:
        now = datetime.now(timezone.utc)
        delta = dt - now
        total_seconds = int(delta.total_seconds())
        minutes = max(0, total_seconds // 60)
        if minutes >= 1440:
            d, rem = divmod(minutes, 1440)
            h = rem // 60
            return f"{d}д {h}ч"
        if fmt == "hours" and minutes >= 60:
            h, m = divmod(minutes, 60)
            return f"{h}ч {m}м"
        return f"{minutes} мин"
    except Exception:
        return ""

def format_hours_until(dt):
    if not dt:
        return ""
    try:
        now = datetime.now(timezone.utc)
        delta = dt - now
        total_seconds = int(delta.total_seconds())
        if total_seconds <= 0:
            return "0ч"
        hours = max(1, total_seconds // 3600)
        return f"{hours}ч"
    except Exception:
        return ""

def create_monochrome_icon(style="logo", display_pct=0.0):
    size = 64
    img = Image.new("RGBA", (size, size), color=(0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if style == "omni":
        d.rounded_rectangle([4, 4, 59, 59], radius=15, outline=(255, 255, 255, 230), width=4)
        font = get_font(36)
        d.text((32, 28), "Ω", fill=(255, 255, 255, 240), font=font, anchor="mm")

    elif style == "digits":
        d.rounded_rectangle([4, 4, 59, 59], radius=15, outline=(255, 255, 255, 200), width=3)
        txt = f"{int(display_pct)}%"
        font_size = 21 if len(txt) <= 3 else (18 if len(txt) <= 4 else 15)
        font = get_font(font_size)
        d.text((32, 31), txt, fill=(255, 255, 255, 240), font=font, anchor="mm")

    elif style == "combo":
        d.rounded_rectangle([4, 4, 59, 59], radius=14, outline=(255, 255, 255, 200), width=3)
        txt = f"{int(display_pct)}%"
        font_9 = get_font(23)
        font_pct = get_font(16)
        d.text((32, 19), "9", fill=(255, 255, 255, 240), font=font_9, anchor="mm")
        d.text((32, 43), txt, fill=(255, 255, 255, 220), font=font_pct, anchor="mm")

    else:  # "logo" (default: 9)
        d.rounded_rectangle([4, 4, 59, 59], radius=15, outline=(255, 255, 255, 230), width=4)
        font = get_font(40)
        d.text((32, 29), "9", fill=(255, 255, 255, 240), font=font, anchor="mm")

    return img

# ----------------- Settings Modal GUI (Tkinter) -----------------

class SettingsDialog:
    def __init__(self, tray):
        self.tray = tray
        self.root = None

    def show(self):
        if self.tray._settings_root:
            try:
                self.tray._settings_root.lift()
                self.tray._settings_root.focus_force()
                return
            except Exception:
                pass

        threading.Thread(target=self._create_window, daemon=True).start()

    def _create_window(self):
        root = tk.Tk()
        self.root = root
        self.tray._settings_root = root

        root.title("Настройки подключения — Antigravity Monitor")
        root.configure(bg="#18181b")

        # Windows 11 Dark Mode Title Bar
        try:
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass

        current_cfg = self.tray.cfg
        preset_var = tk.StringVar(value=current_cfg.get("preset", "omniroute_server"))
        url_var = tk.StringVar(value=current_cfg.get("server_url", "https://omni.cub0d.dev"))
        key_var = tk.StringVar(value=current_cfg.get("api_key", "sk-omni-master-1235"))
        style_var = tk.StringVar(value=current_cfg.get("icon_style", "logo"))
        timer_var = tk.BooleanVar(value=current_cfg.get("show_timer", False))
        interval_var = tk.StringVar(value=str(current_cfg.get("poll_interval", 60)))
        status_var = tk.StringVar(value="Готов к проверке соединения")

        BG = "#18181b"
        CARD = "#27272a"
        TEXT = "#f4f4f5"
        MUTED = "#a1a1aa"
        ACCENT = "#2563eb"

        # 1. Header
        head_frame = tk.Frame(root, bg=BG)
        head_frame.pack(fill="x", padx=16, pady=(10, 4))
        tk.Label(head_frame, text="⚙️ Настройки роутера и подключения", font=("Segoe UI", 11, "bold"), fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(head_frame, text="Выберите сервис (сервер / локально) для мониторинга квот", font=("Segoe UI", 9), fg=MUTED, bg=BG).pack(anchor="w")

        # 2. Preset Selection
        preset_frame = tk.LabelFrame(root, text=" Источник данных (Роутер) ", bg=BG, fg=TEXT, font=("Segoe UI", 9, "bold"), bd=1, relief="solid")
        preset_frame.pack(fill="x", padx=16, pady=3)

        def on_preset_change():
            p = preset_var.get()
            if p == "omniroute_server":
                url_var.set("https://omni.cub0d.dev")
                key_var.set(current_cfg.get("api_key", "sk-omni-master-1235") or "sk-omni-master-1235")
                url_entry.config(state="normal")
                key_entry.config(state="normal")
            elif p == "9router_local":
                url_var.set("http://localhost:20128")
                key_var.set("")
                url_entry.config(state="disabled")
                key_entry.config(state="disabled")
            elif p == "omniroute_local":
                url_var.set("http://localhost:20129")
                key_var.set(current_cfg.get("api_key", "sk-omni-master-1235") or "")
                url_entry.config(state="normal")
                key_entry.config(state="normal")
            elif p == "custom":
                url_entry.config(state="normal")
                key_entry.config(state="normal")

        presets = [
            ("omniroute_server", "🌐 OmniRoute (Сервер — omni.cub0d.dev)"),
            ("9router_local", "💻 9Router (Локально — localhost:20128)"),
            ("omniroute_local", "💻 OmniRoute (Локально — localhost:20129)"),
            ("custom", "⚙️ Свой сервер / кастомный адрес")
        ]

        for val, text in presets:
            rb = tk.Radiobutton(preset_frame, text=text, variable=preset_var, value=val, command=on_preset_change,
                                bg=BG, fg=TEXT, selectcolor="#3f3f46", activebackground=BG, activeforeground=TEXT,
                                font=("Segoe UI", 9))
            rb.pack(anchor="w", padx=10, pady=1)

        # 3. Connection Parameters
        conn_frame = tk.LabelFrame(root, text=" Параметры соединения ", bg=BG, fg=TEXT, font=("Segoe UI", 9, "bold"), bd=1, relief="solid")
        conn_frame.pack(fill="x", padx=16, pady=4)

        tk.Label(conn_frame, text="URL сервиса:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(3, 1))
        url_entry = tk.Entry(conn_frame, textvariable=url_var, bg=CARD, fg=TEXT, insertbackground=TEXT, bd=1, relief="solid", font=("Segoe UI", 9))
        url_entry.pack(fill="x", padx=10, pady=(0, 4))

        tk.Label(conn_frame, text="API Ключ (Bearer / Management Token):", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(1, 1))
        key_entry = tk.Entry(conn_frame, textvariable=key_var, bg=CARD, fg=TEXT, insertbackground=TEXT, bd=1, relief="solid", font=("Segoe UI", 9), show="*")
        key_entry.pack(fill="x", padx=10, pady=(0, 5))

        def toggle_key_show():
            if key_entry.cget("show") == "*":
                key_entry.config(show="")
                show_btn.config(text="Скрыть")
            else:
                key_entry.config(show="*")
                show_btn.config(text="Показать")

        show_btn = tk.Button(conn_frame, text="Показать", command=toggle_key_show, bg=CARD, fg=MUTED, bd=0, font=("Segoe UI", 7), cursor="hand2")
        show_btn.place(relx=1.0, rely=0.82, x=-14, y=-2, anchor="e")

        # 4. Display Options
        opt_frame = tk.LabelFrame(root, text=" Отображение в трее ", bg=BG, fg=TEXT, font=("Segoe UI", 9, "bold"), bd=1, relief="solid")
        opt_frame.pack(fill="x", padx=16, pady=4)

        opt_grid = tk.Frame(opt_frame, bg=BG)
        opt_grid.pack(fill="x", padx=10, pady=3)

        tk.Label(opt_grid, text="Значок:", bg=BG, fg=TEXT, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=1)
        style_cb = ttk.Combobox(opt_grid, textvariable=style_var, values=["logo", "omni", "digits", "combo"], state="readonly", width=12)
        style_cb.grid(row=0, column=1, sticky="w", padx=8, pady=1)

        tk.Label(opt_grid, text="Интервал:", bg=BG, fg=TEXT, font=("Segoe UI", 9)).grid(row=0, column=2, sticky="w", pady=1, padx=(8, 0))
        interval_cb = ttk.Combobox(opt_grid, textvariable=interval_var, values=["30", "60", "120", "300"], state="readonly", width=6)
        interval_cb.grid(row=0, column=3, sticky="w", padx=5, pady=1)

        timer_chk = tk.Checkbutton(opt_frame, text="Отображать время сброса квоты (таймер)", variable=timer_var,
                                   bg=BG, fg=TEXT, selectcolor="#3f3f46", activebackground=BG, activeforeground=TEXT,
                                   font=("Segoe UI", 9))
        timer_chk.pack(anchor="w", padx=10, pady=2)

        # 5. Live Test & Status
        test_frame = tk.Frame(root, bg=BG)
        test_frame.pack(fill="x", padx=16, pady=4)

        status_lbl = tk.Label(test_frame, textvariable=status_var, bg=BG, fg="#38bdf8", font=("Segoe UI", 8), wraplength=290, justify="left")
        status_lbl.pack(side="left", fill="x", expand=True)

        def do_test():
            status_var.set("⏳ Проверка соединения...")
            root.update_idletasks()
            p = preset_var.get()
            u = url_var.get().strip()
            k = key_var.get().strip()

            def _test_worker():
                try:
                    if p == "9router_local":
                        b = get_9router_dir()
                        t = get_cli_token(b)
                        accs = get_accounts_9router(b / "db" / "data.sqlite")
                        status_var.set(f"✅ 9Router активен ({len(accs)} акк.)")
                    else:
                        accs = get_accounts_omniroute(u, k)
                        status_var.set(f"✅ OmniRoute доступен ({len(accs)} акк.)")
                except Exception as e:
                    status_var.set(f"❌ Ошибка: {str(e)[:45]}")

            threading.Thread(target=_test_worker, daemon=True).start()

        test_btn = tk.Button(test_frame, text="⚡ Проверить", command=do_test, bg=CARD, fg=TEXT, activebackground="#3f3f46",
                             activeforeground=TEXT, bd=1, relief="solid", font=("Segoe UI", 9), cursor="hand2", padx=8, pady=2)
        test_btn.pack(side="right")

        # 6. Action Buttons
        btn_frame = tk.Frame(root, bg=BG)
        btn_frame.pack(fill="x", padx=16, pady=(6, 12))

        def on_save():
            p = preset_var.get()
            current_cfg["preset"] = p
            if p == "9router_local":
                current_cfg["server_type"] = "9router"
                current_cfg["server_url"] = "http://localhost:20128"
            else:
                current_cfg["server_type"] = "omniroute"
                current_cfg["server_url"] = url_var.get().strip()
                current_cfg["api_key"] = key_var.get().strip()

            current_cfg["icon_style"] = style_var.get()
            current_cfg["show_timer"] = timer_var.get()
            try:
                current_cfg["poll_interval"] = int(interval_var.get())
            except ValueError:
                current_cfg["poll_interval"] = 60

            save_config(current_cfg)
            self.tray.cfg = current_cfg
            self.tray._last_icon_key = None
            self.tray._apply_icon_image()
            threading.Thread(target=self.tray.update_data, daemon=True).start()
            root.destroy()
            self.tray._settings_root = None

        def on_cancel():
            root.destroy()
            self.tray._settings_root = None

        save_btn = tk.Button(btn_frame, text="💾 Сохранить и применить", command=on_save, bg=ACCENT, fg="#ffffff",
                             activebackground="#1d4ed8", activeforeground="#ffffff", bd=0, font=("Segoe UI", 9, "bold"),
                             padx=15, pady=6, cursor="hand2")
        save_btn.pack(side="left")

        cancel_btn = tk.Button(btn_frame, text="Отмена", command=on_cancel, bg=CARD, fg=MUTED,
                               activebackground="#3f3f46", activeforeground=TEXT, bd=1, relief="solid", font=("Segoe UI", 9),
                               padx=12, pady=5, cursor="hand2")
        cancel_btn.pack(side="right")

        on_preset_change()

        # Compute optimal geometry after all widgets are packed
        root.update_idletasks()
        req_w = max(470, root.winfo_reqwidth() + 20)
        req_h = max(580, root.winfo_reqheight() + 15)
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        x = max(0, (screen_w - req_w) // 2)
        y = max(0, (screen_h - req_h) // 2)
        root.geometry(f"{req_w}x{req_h}+{x}+{y}")
        root.minsize(440, 480)
        root.resizable(True, True)

        def on_close():
            self.tray._settings_root = None
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.focus_force()
        root.mainloop()

# ----------------- Main Tray Application -----------------

class AntigravityTray:
    def __init__(self):
        self.cfg = load_config()

        self.base_dir = None
        self.token = ""
        self.db_path = None
        try:
            self.base_dir = get_9router_dir()
            self.token = get_cli_token(self.base_dir)
            self.db_path = self.base_dir / "db" / "data.sqlite"
        except Exception as e:
            logging.info("Локальный 9Router не обнаружен: %s", e)

        self.icon = None
        self.gemini_sum = 0.0
        self.gemini_weekly_sum = 0.0
        self.claude_sum = 0.0
        self.claude_weekly_sum = 0.0
        self.has_weekly = True
        self.max_possible = 0.0
        self.accounts_data = []
        self.g_earliest_reset = None
        self.gw_earliest_reset = None
        self.c_earliest_reset = None
        self.cw_earliest_reset = None

        self._update_lock = threading.Lock()
        self._last_icon_key = None
        self.is_offline = False
        self._settings_root = None

    def get_display_pct(self):
        main_mod = self.cfg.get("main_model", "gemini")
        if main_mod == "claude":
            return self.claude_sum
        elif main_mod == "avg":
            return (self.gemini_sum + self.claude_sum) / 2
        return self.gemini_sum

    def refresh_token(self):
        if self.base_dir:
            try:
                self.token = get_cli_token(self.base_dir)
            except Exception as e:
                logging.warning("Не удалось перечитать cli токен: %s", e)

    def set_icon_style(self, style):
        self.cfg["icon_style"] = style
        save_config(self.cfg)
        if self.icon:
            self._last_icon_key = None
            self._apply_icon_image()
            self.icon.menu = self.build_menu()

    def set_preset(self, preset):
        self.cfg["preset"] = preset
        if preset == "omniroute_server":
            self.cfg["server_type"] = "omniroute"
            self.cfg["server_url"] = "https://omni.cub0d.dev"
            self.cfg["api_key"] = "sk-omni-master-1235"
        elif preset == "9router_local":
            self.cfg["server_type"] = "9router"
            self.cfg["server_url"] = "http://localhost:20128"
            self.cfg["api_key"] = ""
        elif preset == "omniroute_local":
            self.cfg["server_type"] = "omniroute"
            self.cfg["server_url"] = "http://localhost:20129"
            self.cfg["api_key"] = "sk-omni-master-1235"
        elif preset == "custom":
            pass
        save_config(self.cfg)
        self._last_icon_key = None
        self._apply_icon_image()
        threading.Thread(target=self.update_data, daemon=True).start()

    def open_settings(self, *args):
        if self._settings_root:
            try:
                self._settings_root.after(0, lambda: (self._settings_root.lift(), self._settings_root.focus_force()))
                return
            except Exception:
                pass
        dialog = SettingsDialog(self)
        dialog.show()

    def _apply_icon_image(self):
        if not self.icon:
            return
        style = self.cfg.get("icon_style", "logo")
        pct = self.get_display_pct()
        key = (style, int(pct))
        if key != self._last_icon_key:
            self.icon.icon = create_monochrome_icon(style, pct)
            self._last_icon_key = key

    def build_menu(self):
        fmt = self.cfg.get("timer_format", "minutes")
        show_t = self.cfg.get("show_timer", False)
        st = self.cfg.get("server_type", "omniroute")
        preset = self.cfg.get("preset", "omniroute_server")

        acc_items = []
        for r in self.accounts_data:
            if r.get("error"):
                label = f"[ERR] {r['name']}"
            else:
                reset_txt = ""
                if show_t and r.get("g_reset_short"):
                    reset_txt = f"  {format_short_reset(r.get('g_dt'), fmt)}"
                g_val = int(round(r['gemini']))
                gw_val = int(round(r.get('gemini_weekly', 0.0)))
                c_val = int(round(r['claude']))
                label = f"[{g_val:3d}% G | {gw_val:3d}% W | {c_val:3d}% C]  {r['name']}{reset_txt}"
            acc_items.append(pystray.MenuItem(label, lambda: None, enabled=True))

        if acc_items and self.cfg.get("show_accounts_detail", True):
            acc_submenu = pystray.Menu(*acc_items)
        else:
            acc_submenu = pystray.Menu(pystray.MenuItem("Нет данных", lambda: None, enabled=True))

        g_str = format_time_until(self.g_earliest_reset, fmt) if show_t else ""
        gw_str = format_hours_until(self.gw_earliest_reset) if show_t else ""
        c_str = format_time_until(self.c_earliest_reset, fmt) if show_t else ""
        cw_str = format_hours_until(self.cw_earliest_reset) if show_t else ""

        g_suffix = f" {g_str}" if g_str else ""
        gw_suffix = f" {gw_str}" if gw_str else ""
        c_suffix = f" {c_str}" if c_str else ""
        cw_suffix = f" {cw_str}" if cw_str else ""

        current_style = self.cfg.get("icon_style", "logo")
        style_items = [
            pystray.MenuItem("Логотип '9' (Ч/Б)", lambda: self.set_icon_style("logo"), checked=lambda item: current_style == "logo", radio=True),
            pystray.MenuItem("Логотип 'Ω' (OmniRoute)", lambda: self.set_icon_style("omni"), checked=lambda item: current_style == "omni", radio=True),
            pystray.MenuItem("Только проценты % (Ч/Б)", lambda: self.set_icon_style("digits"), checked=lambda item: current_style == "digits", radio=True),
            pystray.MenuItem("Логотип + проценты (Ч/Б)", lambda: self.set_icon_style("combo"), checked=lambda item: current_style == "combo", radio=True)
        ]

        preset_items = [
            pystray.MenuItem("🌐 OmniRoute (Сервер)", lambda: self.set_preset("omniroute_server"), checked=lambda item: preset == "omniroute_server", radio=True),
            pystray.MenuItem("💻 9Router (Локально)", lambda: self.set_preset("9router_local"), checked=lambda item: preset == "9router_local", radio=True),
            pystray.MenuItem("💻 OmniRoute (Локально)", lambda: self.set_preset("omniroute_local"), checked=lambda item: preset == "omniroute_local", radio=True),
            pystray.MenuItem("⚙️ Свой сервер / кастомный", lambda: self.set_preset("custom"), checked=lambda item: preset == "custom", radio=True),
        ]

        if preset == "9router_local" or st == "9router":
            provider_title = "9Router (Локально)"
        elif preset == "omniroute_server":
            provider_title = "OmniRoute (Сервер)"
        elif preset == "omniroute_local":
            provider_title = "OmniRoute (Локально)"
        else:
            provider_title = "OmniRoute"

        menu_items = [
            pystray.MenuItem("⚙️ Настройки подключения...", self.open_settings, default=True),
            pystray.Menu.SEPARATOR
        ]

        if self.is_offline and not self.accounts_data:
            menu_items.extend([
                pystray.MenuItem(f"⚠️ {provider_title} недоступен", lambda: None, enabled=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Быстрый выбор источника", pystray.Menu(*preset_items)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("🔄 Обновить сейчас", lambda: threading.Thread(target=self.update_data, daemon=True).start()),
                pystray.MenuItem("❌ Выход", self.stop)
            ])
            return pystray.Menu(*menu_items)

        if self.cfg.get("show_gemini", True):
            menu_items.append(pystray.MenuItem(f"Gemini: {self.gemini_sum:.0f}%{g_suffix}", lambda: None, enabled=True))
        if self.cfg.get("show_gemini_weekly", True):
            menu_items.append(pystray.MenuItem(f"Weekly: {self.gemini_weekly_sum:.0f}%{gw_suffix}", lambda: None, enabled=True))
        if self.cfg.get("show_claude", True):
            menu_items.append(pystray.MenuItem(f"Claude: {self.claude_sum:.0f}%{c_suffix}", lambda: None, enabled=True))
        if self.cfg.get("show_claude_weekly", True):
            menu_items.append(pystray.MenuItem(f"Weekly: {self.claude_weekly_sum:.0f}%{cw_suffix}", lambda: None, enabled=True))

        menu_items.append(pystray.Menu.SEPARATOR)
        if self.cfg.get("show_accounts_detail", True):
            menu_items.append(pystray.MenuItem(f"Аккаунты ({len(self.accounts_data)})", acc_submenu))
        menu_items.append(pystray.MenuItem("Быстрый выбор источника", pystray.Menu(*preset_items)))
        menu_items.append(pystray.MenuItem("Стиль значка", pystray.Menu(*style_items)))
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(pystray.MenuItem("🔄 Обновить сейчас", lambda: threading.Thread(target=self.update_data, daemon=True).start()))
        menu_items.append(pystray.MenuItem("❌ Выход", self.stop))

        return pystray.Menu(*menu_items)

    def _query_single_account(self, cid: str, name: str):
        st = self.cfg.get("server_type", "omniroute")
        if st == "omniroute":
            s_url = self.cfg.get("server_url", "https://omni.cub0d.dev")
            k = self.cfg.get("api_key", "sk-omni-master-1235")
            data = fetch_quota_omniroute(s_url, k, cid)
        else:
            data = fetch_quota_9router(cid, self.token)
            if "error" in data and "401" in str(data["error"]):
                self.refresh_token()
                data = fetch_quota_9router(cid, self.token)

        quotas = data.get("quotas", {})

        # 1. Gemini
        g_pct = 0.0
        g_dt = None
        for m in ["gemini-3.1-flash-lite", "gemini_session", "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.7-flash-high", "gemini-pro-agent", "gemini-3-flash"]:
            if m in quotas and "remainingPercentage" in quotas[m]:
                g_pct = float(quotas[m]["remainingPercentage"])
                g_dt = parse_iso_time(quotas[m].get("resetAt"))
                break
        else:
            for k, v in quotas.items():
                if "gemini" in k.lower() and "weekly" not in k.lower() and "remainingPercentage" in v:
                    g_pct = float(v["remainingPercentage"])
                    g_dt = parse_iso_time(v.get("resetAt"))
                    break

        # 2. Gemini weekly
        gw_pct = 0.0
        gw_dt = None
        if "gemini_weekly" in quotas and "remainingPercentage" in quotas["gemini_weekly"]:
            gw_pct = float(quotas["gemini_weekly"]["remainingPercentage"])
            gw_dt = parse_iso_time(quotas["gemini_weekly"].get("resetAt"))

        # 3. Claude
        c_pct = 0.0
        c_dt = None
        for m in ["claude-sonnet-4-6", "claude-opus-4-6-thinking", "claude_gpt_session"]:
            if m in quotas and "remainingPercentage" in quotas[m]:
                c_pct = float(quotas[m]["remainingPercentage"])
                c_dt = parse_iso_time(quotas[m].get("resetAt"))
                break
        else:
            for k, v in quotas.items():
                if "claude" in k.lower() and "weekly" not in k.lower() and "remainingPercentage" in v:
                    c_pct = float(v["remainingPercentage"])
                    c_dt = parse_iso_time(v.get("resetAt"))
                    break

        # 4. Claude weekly
        cw_pct = 0.0
        cw_dt = None
        if "claude_gpt_weekly" in quotas and "remainingPercentage" in quotas["claude_gpt_weekly"]:
            cw_pct = float(quotas["claude_gpt_weekly"]["remainingPercentage"])
            cw_dt = parse_iso_time(quotas["claude_gpt_weekly"].get("resetAt"))

        fmt = self.cfg.get("timer_format", "minutes")
        return {
            "name": name,
            "gemini": g_pct,
            "gemini_weekly": gw_pct,
            "claude": c_pct,
            "claude_weekly": cw_pct,
            "g_dt": g_dt,
            "gw_dt": gw_dt,
            "c_dt": c_dt,
            "cw_dt": cw_dt,
            "g_reset_short": format_short_reset(g_dt, fmt) if g_pct < 99.5 else "",
            "gw_reset_short": format_short_reset(gw_dt, fmt) if gw_pct < 99.5 else "",
            "c_reset_short": format_short_reset(c_dt, fmt) if c_pct < 99.5 else "",
            "cw_reset_short": format_short_reset(cw_dt, fmt) if cw_pct < 99.5 else "",
            "error": data.get("error")
        }

    def update_data(self):
        if not self._update_lock.acquire(blocking=False):
            return

        try:
            st = self.cfg.get("server_type", "omniroute")
            preset = self.cfg.get("preset", "omniroute_server")

            if preset == "9router_local" or st == "9router":
                title_name = "9Router Antigravity"
            elif preset == "omniroute_server":
                title_name = "OmniRoute Antigravity"
            elif preset == "omniroute_local":
                title_name = "OmniRoute (Локально)"
            else:
                title_name = "OmniRoute Antigravity"

            try:
                if st == "omniroute":
                    s_url = self.cfg.get("server_url", "https://omni.cub0d.dev")
                    k = self.cfg.get("api_key", "sk-omni-master-1235")
                    accounts = get_accounts_omniroute(s_url, k)
                else:
                    accounts = get_accounts_9router(self.db_path)
            except Exception as e:
                logging.warning("Не удалось получить список аккаунтов (%s): %s", st, e)
                self.is_offline = True
                if self.icon:
                    self.icon.title = f"{title_name}: ожидание соединения..."[:127]
                    self.icon.menu = self.build_menu()
                return

            if not accounts:
                self.accounts_data = []
                self.gemini_sum = 0.0
                self.gemini_weekly_sum = 0.0
                self.claude_sum = 0.0
                self.claude_weekly_sum = 0.0
                self.max_possible = 0.0
                if self.icon:
                    self.icon.title = f"{title_name}: нет активных аккаунтов"[:127]
                    self.icon.menu = self.build_menu()
                return

            # Parallel fetch with max 8 workers
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(accounts))) as executor:
                futures = [executor.submit(self._query_single_account, cid, name) for cid, name in accounts]
                for f in futures:
                    results.append(f.result())

            all_errors = all(bool(r.get("error")) for r in results)
            if all_errors:
                self.is_offline = True
                logging.warning("Все запросы к %s завершились ошибкой.", title_name)
                if self.icon:
                    self.icon.title = f"{title_name}: сервис недоступен"[:127]
                    self.icon.menu = self.build_menu()
                return

            self.is_offline = False
            now = datetime.now(timezone.utc)
            g_sum = 0.0
            gw_sum = 0.0
            c_sum = 0.0
            cw_sum = 0.0
            g_earliest = None
            gw_earliest = None
            c_earliest = None
            cw_earliest = None

            for r in results:
                g_sum += r["gemini"]
                gw_sum += r.get("gemini_weekly", 0.0)
                c_sum += r["claude"]
                cw_sum += r.get("claude_weekly", 0.0)

                if r["g_dt"] and r["g_dt"] > now and r["gemini"] < 99.5:
                    if g_earliest is None or r["g_dt"] < g_earliest:
                        g_earliest = r["g_dt"]

                if r.get("gw_dt") and r["gw_dt"] > now and r.get("gemini_weekly", 100.0) < 99.5:
                    if gw_earliest is None or r["gw_dt"] < gw_earliest:
                        gw_earliest = r["gw_dt"]

                if r["c_dt"] and r["c_dt"] > now and r["claude"] < 99.5:
                    if c_earliest is None or r["c_dt"] < c_earliest:
                        c_earliest = r["c_dt"]

                if r.get("cw_dt") and r["cw_dt"] > now and r.get("claude_weekly", 100.0) < 99.5:
                    if cw_earliest is None or r["cw_dt"] < cw_earliest:
                        cw_earliest = r["cw_dt"]

            self.accounts_data = results
            self.gemini_sum = g_sum
            self.gemini_weekly_sum = gw_sum
            self.claude_sum = c_sum
            self.claude_weekly_sum = cw_sum
            self.g_earliest_reset = g_earliest
            self.gw_earliest_reset = gw_earliest
            self.c_earliest_reset = c_earliest
            self.cw_earliest_reset = cw_earliest
            self.max_possible = len(accounts) * 100.0

            if self.icon:
                self._apply_icon_image()
                fmt = self.cfg.get("timer_format", "minutes")
                show_t = self.cfg.get("show_timer", False)

                title_lines = [f"{title_name} ({len(accounts)} акк.):"]
                if self.cfg.get("show_gemini", True):
                    g_str = format_time_until(self.g_earliest_reset, fmt) if show_t else ""
                    g_suffix = f" {g_str}" if g_str else ""
                    title_lines.append(f"Gemini: {self.gemini_sum:.0f}%{g_suffix}")
                if self.cfg.get("show_gemini_weekly", True):
                    gw_str = format_hours_until(self.gw_earliest_reset) if show_t else ""
                    gw_suffix = f" {gw_str}" if gw_str else ""
                    title_lines.append(f"Weekly: {self.gemini_weekly_sum:.0f}%{gw_suffix}")
                if self.cfg.get("show_claude", True):
                    c_str = format_time_until(self.c_earliest_reset, fmt) if show_t else ""
                    c_suffix = f" {c_str}" if c_str else ""
                    title_lines.append(f"Claude: {self.claude_sum:.0f}%{c_suffix}")
                if self.cfg.get("show_claude_weekly", True):
                    cw_str = format_hours_until(self.cw_earliest_reset) if show_t else ""
                    cw_suffix = f" {cw_str}" if cw_str else ""
                    title_lines.append(f"Weekly: {self.claude_weekly_sum:.0f}%{cw_suffix}")

                self.icon.title = "\n".join(title_lines)[:127]
                self.icon.menu = self.build_menu()

        except Exception as e:
            logging.exception("Ошибка в цикле update_data: %s", e)
        finally:
            self._update_lock.release()

    def poll_loop(self):
        while True:
            self.update_data()
            interval = int(self.cfg.get("poll_interval", 60))
            time.sleep(max(15, interval))

    def stop(self, *args):
        logging.info("Остановка AntigravityTray.")
        if self._settings_root:
            try:
                self._settings_root.destroy()
            except Exception:
                pass
        if self.icon:
            self.icon.stop()

    def run(self):
        logging.info("Запуск AntigravityTray...")
        style = self.cfg.get("icon_style", "logo")
        initial_img = create_monochrome_icon(style, 0.0)
        self.icon = pystray.Icon(
            "antigravity_tray",
            initial_img,
            "Antigravity (загрузка...)",
            pystray.Menu(pystray.MenuItem("Загрузка...", None, enabled=False))
        )
        threading.Thread(target=self.poll_loop, daemon=True).start()
        self.icon.run()

if __name__ == "__main__":
    # Single instance mutex check to prevent multiple processes running concurrently
    mutex_name = "Local\AntigravityQuotaTrayMutex"
    ERROR_ALREADY_EXISTS = 183
    _app_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        import sys
        sys.exit(0)

    app = AntigravityTray()
    app.run()
