#!/usr/bin/env python3
"""
Antigravity 9Router Tray Monitor
Monochrome Windows 11 system tray monitor for 9Router Antigravity account quotas.
Tracks aggregated pool percentage and earliest quota reset times with full GUI Settings.
"""

import ctypes

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
except Exception as _e:
    logging.warning("Не удалось настроить тёмный режим меню: %s", _e)

# ponytail: Pure pystray tray monitor with ThreadPoolExecutor & Tkinter settings modal.

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
    "icon_style": "logo",          # "logo", "digits", "combo"
    "theme": "dark",              # "dark", "light"
    "show_gemini": True,
    "show_claude": True,
    "show_accounts_detail": True,
    "timer_format": "minutes",    # "minutes" (135 мин) or "hours" (2ч 15м)
    "show_timer": True,
    "poll_interval": 60,          # seconds: 30, 60, 120, 300
    "main_model": "gemini"        # "gemini", "claude", "avg"
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

def get_accounts(db_path: Path):
    with sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True, timeout=10) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name FROM providerConnections WHERE provider='antigravity' AND isActive=1 ORDER BY createdAt ASC;")
        return c.fetchall()

def fetch_quota(connection_id: str, token: str, port: int = 20128):
    url = f"http://localhost:{port}/api/usage/{connection_id}"
    req = urllib.request.Request(url)
    req.add_header("x-9r-cli-token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

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
        if fmt == "hours" and minutes >= 60:
            h, m = divmod(minutes, 60)
            return f"{h}ч {m}м"
        return f"{minutes} мин"
    except Exception:
        return ""

def create_monochrome_icon(style="logo", display_pct=0.0):
    size = 64
    img = Image.new("RGBA", (size, size), color=(0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if style == "logo":
        d.rounded_rectangle([4, 4, 59, 59], radius=15, outline=(255, 255, 255, 230), width=4)
        font = get_font(40)
        d.text((32, 29), "9", fill=(255, 255, 255, 240), font=font, anchor="mm")

    elif style == "digits":
        d.rounded_rectangle([4, 4, 59, 59], radius=15, outline=(255, 255, 255, 200), width=3)
        txt = f"{int(display_pct)}%"
        font_size = 21 if len(txt) <= 3 else (18 if len(txt) <= 4 else 15)
        font = get_font(font_size)
        d.text((32, 31), txt, fill=(255, 255, 255, 240), font=font, anchor="mm")

    else:  # "combo"
        d.rounded_rectangle([4, 4, 59, 59], radius=14, outline=(255, 255, 255, 200), width=3)
        txt = f"{int(display_pct)}%"
        font_9 = get_font(23)
        font_pct = get_font(16)
        d.text((32, 19), "9", fill=(255, 255, 255, 240), font=font_9, anchor="mm")
        d.text((32, 43), txt, fill=(255, 255, 255, 220), font=font_pct, anchor="mm")

    return img

class AntigravityTray:
    def __init__(self):
        self.base_dir = get_9router_dir()
        self.token = get_cli_token(self.base_dir)
        self.db_path = self.base_dir / "db" / "data.sqlite"
        
        self.cfg = load_config()

        self.icon = None
        self.gemini_sum = 0.0
        self.claude_sum = 0.0
        self.max_possible = 0.0
        self.accounts_data = []
        self.g_earliest_reset = None
        self.c_earliest_reset = None
        
        self._update_lock = threading.Lock()
        self._last_icon_key = None
        self.is_offline = False

    def get_display_pct(self):
        main_mod = self.cfg.get("main_model", "gemini")
        if main_mod == "claude":
            return self.claude_sum
        elif main_mod == "avg":
            return (self.gemini_sum + self.claude_sum) / 2
        return self.gemini_sum

    def refresh_token(self):
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
        show_t = self.cfg.get("show_timer", True)
        acc_items = []
        for r in self.accounts_data:
            if r.get("error"):
                label = f"[ERR] {r['name']}"
            else:
                reset_txt = ""
                if show_t and r.get("g_reset_short"):
                    reset_txt = f"  {format_short_reset(r.get('g_dt'), fmt)}"
                g_val = int(round(r['gemini']))
                c_val = int(round(r['claude']))
                label = f"[{g_val:3d}% G | {c_val:3d}% C]  {r['name']}{reset_txt}"
            acc_items.append(pystray.MenuItem(label, lambda: None, enabled=True))

        # Safe submenu
        if acc_items and self.cfg.get("show_accounts_detail", True):
            acc_submenu = pystray.Menu(*acc_items)
        else:
            acc_submenu = pystray.Menu(pystray.MenuItem("Нет данных", lambda: None, enabled=True))

        g_str = format_time_until(self.g_earliest_reset, fmt) if show_t else ""
        c_str = format_time_until(self.c_earliest_reset, fmt) if show_t else ""
        g_suffix = f" {g_str}" if g_str else ""
        c_suffix = f" {c_str}" if c_str else ""

        current_style = self.cfg.get("icon_style", "logo")
        style_items = [
            pystray.MenuItem("Логотип 9Router (Ч/Б)", lambda: self.set_icon_style("logo"), checked=lambda item: current_style == "logo", radio=True),
            pystray.MenuItem("Только проценты % (Ч/Б)", lambda: self.set_icon_style("digits"), checked=lambda item: current_style == "digits", radio=True),
            pystray.MenuItem("Логотип + проценты (Ч/Б)", lambda: self.set_icon_style("combo"), checked=lambda item: current_style == "combo", radio=True)
        ]

        if self.is_offline and not self.accounts_data:
            menu_items = [
                pystray.MenuItem("⚠️ 9Router недоступен", lambda: None, enabled=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("🔄 Обновить сейчас", lambda: threading.Thread(target=self.update_data, daemon=True).start()),
                pystray.MenuItem("❌ Выход", self.stop)
            ]
            return pystray.Menu(*menu_items)

        menu_items = []
        if self.cfg.get("show_gemini", True):
            menu_items.append(pystray.MenuItem(f"Gemini: {self.gemini_sum:.0f}%{g_suffix}", lambda: None, enabled=True))
        if self.cfg.get("show_claude", True):
            menu_items.append(pystray.MenuItem(f"Claude: {self.claude_sum:.0f}%{c_suffix}", lambda: None, enabled=True))

        menu_items.append(pystray.Menu.SEPARATOR)
        if self.cfg.get("show_accounts_detail", True):
            menu_items.append(pystray.MenuItem(f"Аккаунты ({len(self.accounts_data)})", acc_submenu))
        menu_items.append(pystray.MenuItem("Стиль значка", pystray.Menu(*style_items)))
        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(pystray.MenuItem("🔄 Обновить сейчас", lambda: threading.Thread(target=self.update_data, daemon=True).start()))
        menu_items.append(pystray.MenuItem("❌ Выход", self.stop))

        return pystray.Menu(*menu_items)

    def _query_single_account(self, cid: str, name: str):
        data = fetch_quota(cid, self.token)
        if "error" in data and "401" in str(data["error"]):
            self.refresh_token()
            data = fetch_quota(cid, self.token)

        quotas = data.get("quotas", {})
        g_pct = 0.0
        g_dt = None
        for m in ["gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.7-flash-high", "gemini-pro-agent"]:
            if m in quotas and "remainingPercentage" in quotas[m]:
                g_pct = float(quotas[m]["remainingPercentage"])
                g_dt = parse_iso_time(quotas[m].get("resetAt"))
                break

        c_pct = 0.0
        c_dt = None
        for m in ["claude-sonnet-4-6", "claude-opus-4-6-thinking"]:
            if m in quotas and "remainingPercentage" in quotas[m]:
                c_pct = float(quotas[m]["remainingPercentage"])
                c_dt = parse_iso_time(quotas[m].get("resetAt"))
                break

        fmt = self.cfg.get("timer_format", "minutes")
        return {
            "name": name,
            "gemini": g_pct,
            "claude": c_pct,
            "g_dt": g_dt,
            "c_dt": c_dt,
            "g_reset_short": format_short_reset(g_dt, fmt) if g_pct < 99.5 else "",
            "error": data.get("error")
        }

    def update_data(self):
        if not self._update_lock.acquire(blocking=False):
            return

        try:
            try:
                accounts = get_accounts(self.db_path)
            except Exception as e:
                logging.warning("Не удалось прочитать аккаунты из SQLite: %s", e)
                self.is_offline = True
                if self.icon:
                    self.icon.title = "9Router: ожидание базы данных..."[:127]
                    self.icon.menu = self.build_menu()
                return

            if not accounts:
                self.accounts_data = []
                self.gemini_sum = 0.0
                self.claude_sum = 0.0
                self.max_possible = 0.0
                if self.icon:
                    self.icon.title = "9Router: нет активных аккаунтов"[:127]
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
                logging.warning("Все запросы к 9Router завершились ошибкой.")
                if self.icon:
                    self.icon.title = "9Router: сервис запускается / офлайн"[:127]
                    self.icon.menu = self.build_menu()
                return

            self.is_offline = False
            now = datetime.now(timezone.utc)
            g_sum = 0.0
            c_sum = 0.0
            g_earliest = None
            c_earliest = None

            for r in results:
                g_sum += r["gemini"]
                c_sum += r["claude"]

                if r["g_dt"] and r["g_dt"] > now and r["gemini"] < 99.5:
                    if g_earliest is None or r["g_dt"] < g_earliest:
                        g_earliest = r["g_dt"]

                if r["c_dt"] and r["c_dt"] > now and r["claude"] < 99.5:
                    if c_earliest is None or r["c_dt"] < c_earliest:
                        c_earliest = r["c_dt"]

            self.accounts_data = results
            self.gemini_sum = g_sum
            self.claude_sum = c_sum
            self.g_earliest_reset = g_earliest
            self.c_earliest_reset = c_earliest
            self.max_possible = len(accounts) * 100.0

            if self.icon:
                self._apply_icon_image()
                fmt = self.cfg.get("timer_format", "minutes")
                show_t = self.cfg.get("show_timer", True)

                title_lines = [f"9Router Antigravity ({len(accounts)} акк.):"]
                if self.cfg.get("show_gemini", True):
                    g_str = format_time_until(self.g_earliest_reset, fmt) if show_t else ""
                    g_suffix = f" {g_str}" if g_str else ""
                    title_lines.append(f"Gemini: {self.gemini_sum:.0f}%{g_suffix}")
                if self.cfg.get("show_claude", True):
                    c_str = format_time_until(self.c_earliest_reset, fmt) if show_t else ""
                    c_suffix = f" {c_str}" if c_str else ""
                    title_lines.append(f"Claude: {self.claude_sum:.0f}%{c_suffix}")

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
        if self.icon:
            self.icon.stop()

    def run(self):
        logging.info("Запуск AntigravityTray...")
        style = self.cfg.get("icon_style", "logo")
        initial_img = create_monochrome_icon(style, 0.0)
        self.icon = pystray.Icon(
            "antigravity_tray",
            initial_img,
            "9Router Antigravity (загрузка...)",
            pystray.Menu(pystray.MenuItem("Загрузка...", None, enabled=False))
        )
        threading.Thread(target=self.poll_loop, daemon=True).start()
        self.icon.run()

if __name__ == "__main__":
    # Single instance mutex check to prevent multiple processes running concurrently
    mutex_name = "Local\\AntigravityQuotaTrayMutex"
    ERROR_ALREADY_EXISTS = 183
    _app_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        import sys
        sys.exit(0)

    app = AntigravityTray()
    app.run()
