"""Пик-контроллер — трей-приложение Windows (PySide6/Qt + локальный HTTP-сервер UI).

Запуск:  py -3.12 app/main.py          (с консолью, для отладки)
         pyw -3.12 app/main.py         (без консоли)
Проверка данных без окна:  py -3.12 app/main.py --selftest
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- пути
if getattr(sys, "frozen", False):
    # запущено как собранный exe: ресурсы — в бандле, данные — рядом с exe
    BASE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    ROOT = Path(sys.executable).resolve().parent      # data/, config.json — рядом с exe
    UI_DIR = BASE / "ui"
else:
    BASE = Path(__file__).resolve().parent            # app/
    ROOT = BASE.parent                                # корень проекта
    UI_DIR = BASE / "ui"
DATA = ROOT / "data"
LOG_PATH = DATA / "app.log"

sys.path.insert(0, str(BASE))
from core import schedule, store                  # noqa: E402
from sources import deepseek, glm                 # noqa: E402

# ---------------------------------------------------------------- Qt
from PySide6.QtCore import QObject, QPoint, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap, QPolygonF, QShortcut
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QPen
from PySide6.QtWebEngineWidgets import QWebEngineView

# ---------------------------------------------------------------- конфигурация
DEFAULT_CONFIG = {
    "refresh_interval_minutes": 60,
    "glm_prices": {
        "lite_price": "$18/мес", "lite_note": "официальная",
        "pro_price": "$80/мес", "pro_note": "≈, не офиц.",
        "max_price": "$168/мес", "max_note": "≈, не офиц.",
    },
    "glm_static_plans": {
        "tstd": {"h5": "15 000", "week": "66 000", "price": "—/мес", "note": "за место",
                 "tokens": "Team Standard: место в команде"},
        "tprem": {"h5": "35 000", "week": "155 000", "price": "—/мес", "note": "за место",
                  "tokens": "Team Premium: место в команде"},
    },
}
CONFIG_PATH = ROOT / "config.json"


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        for k, v in json.loads(CONFIG_PATH.read_text(encoding="utf-8")).items():
            if not k.startswith("_") and k in cfg:
                cfg[k] = v
    except Exception:
        pass
    return cfg


CONFIG = load_config()
REASON_TEXT = {"weekend": "выходной", "cn_holiday": "китайский праздник", "offpeak_hours": "вне пиковых часов"}
DS_LABELS = {"input_cache_hit": "Вход · кэш-хит", "input": "Вход · без кэша", "output": "Выход"}
SOURCE_NAMES = {"glm": "docs.z.ai", "ds": "api-docs.deepseek.com"}

# ---------------------------------------------------------------- журнал
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH), level=logging.INFO, encoding="utf-8",
    format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
)


def log_exc(prefix: str) -> None:
    logging.error("%s\n%s", prefix, traceback.format_exc())


# ---------------------------------------------------------------- состояние
state: dict = {
    "snapshot": None, "visible": False, "shown_at": 0.0, "ever_shown": False,
    "last_refresh": 0.0, "refreshing": False, "icon_pt": (0, 0),
}
lock = threading.Lock()
app = None
win = None
tray = None
signals = None
_mutex = None
_ICONS: dict = {}


def fmt_money(v: float) -> str:
    return f"${v:.2f}" if v >= 0.1 else f"${v:.3f}"


def fmt_thousands(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def fmt_updated(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except Exception:
        return None
    today = datetime.now().date()
    day = "сегодня" if dt.date() == today else ("вчера" if dt.date() == today - timedelta(days=1) else dt.strftime("%d.%m"))
    return f"{day}, {dt:%H:%M}"


# ---------------------------------------------------------------- снапшот
def build_snapshot(cache: dict) -> dict:
    now = datetime.now(timezone.utc)
    holidays = schedule.load_holidays()
    snap: dict = {
        "generated_epoch": now.timestamp(), "refreshing": state["refreshing"],
        "any_error": False, "banner_text": None, "updated_text": None,
        "glm": {"status": "nodata"}, "ds": {"status": "nodata"},
    }

    src = cache.get("glm", {})
    d = src.get("data")
    g: dict = {"status": "ok" if not src.get("error") else ("stale" if d else "error"),
               "error": src.get("error"), "peak": False, "windows_local": [],
               "next_change_epoch": None, "reason_text": None}
    if d:
        ps = schedule.peak_state(d["peak_windows_sgt"], d["tz"], [0, 1, 2, 3, 4], None, now)
        g.update(peak=ps["peak"], windows_local=ps["windows_local"],
                 next_change_epoch=ps["next_change"].timestamp() if ps["next_change"] else None,
                 reason_text=REASON_TEXT.get(ps["reason"]))
        pct = d["offpeak_pct"]
        g["burn"] = {"off_text": f"×{pct / 100:g} · не-пик", "peak_text": "×1.0 · пик"}
        plans = {}
        for key in ("lite", "pro", "max"):
            t = d["tiers"][key]
            est = d.get("token_estimates", {}).get(key)
            plans[key] = {
                "h5": fmt_thousands(t["h5"]), "week": fmt_thousands(t["week"]),
                "price": CONFIG["glm_prices"].get(key + "_price", "—/мес"),
                "note": CONFIG["glm_prices"].get(key + "_note", ""),
                "tokens": (f"≈ {est} млн токенов в неделю (GLM-5.3, кэш-хит 95%)" if est else "оценка недоступна"),
            }
        plans.update(CONFIG["glm_static_plans"])
        g["plans"] = plans
        g["models"] = d["models"]
    snap["glm"] = g

    src = cache.get("ds", {})
    d = src.get("data")
    s: dict = {"status": "ok" if not src.get("error") else ("stale" if d else "error"),
               "error": src.get("error"), "peak": False, "windows_local": [],
               "next_change_epoch": None, "reason_text": None}
    if d:
        hol = holidays if d.get("cn_holidays_offpeak") else None
        ps = schedule.peak_state(d["peak_windows_utc"], "UTC", [0, 1, 2, 3, 4], hol, now)
        models = [{
            "alias": m["alias"], "version": m["version"],
            "rows": [[DS_LABELS[k], fmt_money(m["prices"][k]["offpeak"]), fmt_money(m["prices"][k]["peak"])]
                     for k in ("input_cache_hit", "input", "output")],
        } for m in d["models"]]
        s.update(models=models, peak=ps["peak"], windows_local=ps["windows_local"],
                 next_change_epoch=ps["next_change"].timestamp() if ps["next_change"] else None,
                 reason_text=REASON_TEXT.get(ps["reason"]))
    snap["ds"] = s

    times = [src.get("fetched_at") for src in (cache.get("glm", {}), cache.get("ds", {})) if src.get("fetched_at")]
    snap["updated_text"] = fmt_updated(max(times) if times else None)
    errs = [f"{SOURCE_NAMES[k]}: {cache[k]['error']}" for k in ("glm", "ds") if cache.get(k, {}).get("error")]
    if errs:
        snap["any_error"] = True
        snap["banner_text"] = "; ".join(errs) + ". Показаны последние корректные данные — возможно, сайт изменился и приложение нужно поправить."
    return snap


# ---------------------------------------------------------------- сеть / кэш
def fetch_all() -> dict:
    results = {}
    for key, mod in (("glm", glm), ("ds", deepseek)):
        try:
            results[key] = mod.refresh()
            logging.info("%s: обновлено", key)
        except Exception as e:
            results[key] = {"error": f"{type(e).__name__}: {e}"}
            logging.error("%s: ошибка обновления: %s", key, e)
        time.sleep(1.0)
    return results


def merge_cache(results: dict) -> dict:
    cache = store.load()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key in ("glm", "ds"):
        res = results[key]
        prev = cache.get(key, {})
        if "error" in res:
            cache[key] = {"data": prev.get("data"), "hashes": prev.get("hashes", {}),
                          "fetched_at": prev.get("fetched_at"), "error": res["error"],
                          "checked_at": now_iso, "warnings": prev.get("warnings", [])}
        else:
            cache[key] = {"data": res["data"], "hashes": res["hashes"], "fetched_at": res["fetched_at"],
                          "error": None, "checked_at": now_iso, "warnings": res.get("warnings", [])}
    store.save(cache)
    return cache


def refresh_worker() -> None:
    with lock:
        if state["refreshing"]:
            return
        state["refreshing"] = True
    try:
        cache = merge_cache(fetch_all())
    except Exception:
        log_exc("refresh_worker: непредвиденная ошибка")
        cache = store.load()
    with lock:
        state["refreshing"] = False
        state["last_refresh"] = time.time()
        state["snapshot"] = build_snapshot(cache)
    if signals:
        signals.icon_update.emit(icon_color())


def scheduler() -> None:
    interval = CONFIG["refresh_interval_minutes"] * 60
    while True:
        time.sleep(30)
        if time.time() - state["last_refresh"] >= interval:
            refresh_worker()


def icon_color() -> str:
    s = state["snapshot"]
    if not s or s["any_error"]:
        return "yellow"
    if s["glm"].get("peak") or s["ds"].get("peak"):
        return "red"
    return "green"


def refresh_status() -> None:
    """Пересчёт пик-статусов по времени (из кэша, без сети) —
    чтобы иконка трея и пилюли реагировали на наступление пика в течение минуты."""
    state["snapshot"] = build_snapshot(store.load())
    if signals:
        signals.icon_update.emit(icon_color())


# ---------------------------------------------------------------- автозапуск
def _startup_bat() -> Path:
    return (Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup" / "peak-controller.bat")


def autostart_enabled() -> bool:
    return _startup_bat().exists()


def set_autostart(on: bool) -> None:
    bat = _startup_bat()
    try:
        if not on:
            if bat.exists():
                bat.unlink()
                logging.info("автозапуск выключен")
            return
        exe = ROOT / "dist" / "PeakController" / "PeakController.exe"
        if exe.exists():  # собранный exe — приоритет
            bat.write_text(f'@echo off\r\nstart "" "{exe}"\r\n', encoding="utf-8")
        else:
            pyexe = Path(sys.executable)
            py = pyexe.with_name("pythonw.exe")
            if py.exists() or pyexe.name == "pythonw.exe":
                # pythonw рядом с текущим интерпретатором (в т.ч. .venv) — уже нужной
                # версии, поэтому флаг -3.12 ему передавать нельзя (он не понимает «-3»)
                bat.write_text(f'@echo off\r\nstart "" "{py}" "{BASE / "main.py"}"\r\n', encoding="utf-8")
            else:
                py = shutil.which("pyw") or sys.executable
                bat.write_text(f'@echo off\r\nstart "" "{py}" -3.12 "{BASE / "main.py"}"\r\n', encoding="utf-8")
        logging.info("автозапуск включён: %s", bat)
    except Exception:
        log_exc("set_autostart")


# ---------------------------------------------------------------- HTTP-сервер UI
class UiHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # без доступа в лог
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj) -> None:
        self._send(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/api/state", "/api/"):
            self._json(state["snapshot"] or {})
            return
        static = {"/": "index.html", "/index.html": "index.html",
                  "/app.css": "app.css", "/app.js": "app.js"}
        name = static.get(path)
        if name:
            try:
                body = (UI_DIR / name).read_bytes()
                ctype = ("text/html; charset=utf-8" if name.endswith(".html")
                         else "text/css; charset=utf-8" if name.endswith(".css")
                         else "application/javascript; charset=utf-8")
                self._send(200, body, ctype)
            except Exception:
                self._send(404, b"not found", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b"{}"
        except Exception:
            body = b"{}"
        if path == "/api/refresh":
            threading.Thread(target=refresh_worker, daemon=True).start()
            self._json({"ok": True})
        elif path == "/api/hide":
            hide_popup()
            self._json({"ok": True})
        elif path == "/api/mouse_left":
            signals.mouse_left.emit()  # queued в GUI-поток
            self._json({"ok": True})
        elif path == "/api/fit":
            try:
                if not state.get("ever_shown"):
                    # до первого показа webview не отрисован в реальную ширину —
                    # замеры мусорные (контент сжимается в узкую колонку)
                    self._json({"ok": True, "ignored": True})
                    return
                data = json.loads(body)
                h = max(560, min(int(data.get("h", 810)), 1100))
                logging.debug("fit: measured=%s -> h=%s", data.get("m"), h)
                signals.fit.emit(h)
            except Exception:
                pass
            self._json({"ok": True})
        else:
            self._send(404, b"not found", "text/plain")


def start_ui_server() -> str:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), UiHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    logging.info("UI-сервер: http://127.0.0.1:%d", port)
    return f"http://127.0.0.1:{port}/index.html"


# ---------------------------------------------------------------- иконка трея
def make_icon(color: str) -> QIcon:
    """Скруглённый квадрат с молнией («пик»)."""
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 16, 16)
    p.setPen(QPen(QColor(255, 255, 255, 70), 2))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 16, 16)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 235))
    p.drawPolygon(QPolygonF([QPointF(37, 9), QPointF(17, 36), QPointF(28, 36),
                             QPointF(25, 55), QPointF(47, 26), QPointF(34, 26), QPointF(39, 9)]))
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- окошко
class Signals(QObject):
    fit = Signal(int)
    icon_update = Signal(str)
    mouse_left = Signal()
    show_popup = Signal()
    hide_popup = Signal()


class Popup(QWidget):
    """Безрамочное полупрозрачное окошко (скругления — из CSS)."""

    def __init__(self, url: str):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.web = QWebEngineView(self)
        self.web.page().setBackgroundColor(Qt.transparent)
        self.web.setMinimumSize(0, 0)  # иначе минимальный размер webview раздувает окно
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.addWidget(self.web)
        self.web.load(QUrl(url))
        self.web.loadFinished.connect(lambda ok: logging.info("webview: загрузка %s", "ok" if ok else "ОШИБКА"))
        self.setFixedSize(456, 860)

        QShortcut(QKeySequence("Esc"), self, activated=hide_popup)

    def apply_fit(self, h: int) -> None:
        self.setFixedHeight(h)
        if state["visible"] and self.isVisible():
            self._place()

    def _place(self) -> None:
        """Низ окошка — ровно над панелью задач, горизонталь — по точке клика в трее."""
        cursor = state.get("icon_pt", (0, 0))
        screen = QGuiApplication.screenAt(QPoint(*cursor)) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = max(avail.left() + 4, min(cursor[0] - w // 2, avail.right() - w - 4))
        y = max(avail.top() + 4, avail.bottom() - h - 4)
        self.move(x, y)

    def show_at_cursor(self) -> None:
        cursor = QCursor.pos()
        state["icon_pt"] = (cursor.x(), cursor.y())
        state["ever_shown"] = True
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()
        state["visible"] = True
        state["shown_at"] = time.time()
        logging.info("popup: показан, pos=(%d, %d) size=%dx%d",
                     self.x(), self.y(), self.width(), self.height())


def show_popup() -> None:
    """Потокобезопасно: просит GUI-поток показать окошко."""
    if signals:
        signals.show_popup.emit()


def hide_popup() -> None:
    """Потокобезопасно: просит GUI-поток скрыть окошко."""
    if signals:
        signals.hide_popup.emit()


def _do_show_popup() -> None:
    if win is None:
        return
    win.show_at_cursor()
    tray.setIcon(_ICONS.get(icon_color(), _ICONS["green"]))


def _do_hide_popup() -> None:
    if win is None or not state["visible"]:
        return
    state["visible"] = False
    win.hide()
    logging.info("popup: скрыт")


def _maybe_hide_after_leave() -> None:
    """Курсор покинул окошко (из JS): скрыть, если он не вернулся и не ушёл к иконке."""
    if not state["visible"] or win is None:
        return
    geo = win.frameGeometry().adjusted(-8, -8, 8, 8)
    cursor = QCursor.pos()
    ip = state.get("icon_pt", (cursor.x(), cursor.y()))
    bridge = (cursor.x() >= geo.left() - 70 and cursor.x() <= geo.right() + 70
              and cursor.y() >= geo.bottom() and abs(cursor.x() - ip[0]) <= 90
              and cursor.y() <= ip[1] + 60)
    if not geo.contains(cursor) and not bridge:
        logging.info("popup: скрыт — курсор ушёл (mouseleave)")
        _do_hide_popup()


def watcher_tick() -> None:
    """Один проход проверки (работает в GUI-потоке по таймеру)."""
    if not state["visible"] or win is None:
        return
    geo = win.frameGeometry().adjusted(-12, -12, 12, 12)
    cursor = QCursor.pos()
    if geo.contains(cursor):
        return
    ip = state.get("icon_pt", (cursor.x(), cursor.y()))
    bridge = (cursor.x() >= geo.left() - 70 and cursor.x() <= geo.right() + 70
              and cursor.y() >= geo.bottom() and abs(cursor.x() - ip[0]) <= 90
              and cursor.y() <= ip[1] + 60)
    if bridge:
        return
    if time.time() - state["shown_at"] > 0.6:
        logging.info("popup: скрыт — курсор ушёл")
        _do_hide_popup()


# ---------------------------------------------------------------- точка входа
def _main() -> None:
    global app, win, tray, signals, _ICONS, _mutex
    DATA.mkdir(exist_ok=True)

    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "peak-controller-singleton")
    if ctypes.windll.kernel32.GetLastError() == 183:
        logging.info("второй экземпляр запущен — выходим")
        sys.exit(0)

    logging.info("=== запуск пик-контроллера ===")
    cache = store.load()
    state["snapshot"] = build_snapshot(cache)

    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # имя приложения для панели задач/диспетчера (AppUserModelID)
    try:
        _shell32 = ctypes.windll.shell32
        _shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        _shell32.SetCurrentProcessExplicitAppUserModelID("zai.peak.controller")
    except Exception:
        pass
    app.setWindowIcon(make_icon("#3ddc97"))  # иконка-молния для всех окон

    signals = Signals()
    signals.fit.connect(lambda h: win.apply_fit(h))
    signals.icon_update.connect(lambda c: tray.setIcon(_ICONS.get(c, _ICONS["green"])))
    signals.mouse_left.connect(lambda: QTimer.singleShot(450, _maybe_hide_after_leave))
    signals.show_popup.connect(_do_show_popup)
    signals.hide_popup.connect(_do_hide_popup)

    url = start_ui_server()
    win = Popup(url)

    _ICONS = {"green": make_icon("#3ddc97"), "red": make_icon("#ff6b6b"), "yellow": make_icon("#f5b041")}
    tray = QSystemTrayIcon(_ICONS["green"])
    tray.setToolTip("Пик-контроллер")

    menu = QMenu()
    act_show = QAction("Показать", menu)
    act_show.triggered.connect(show_popup)
    act_refresh = QAction("Обновить сейчас", menu)
    act_refresh.triggered.connect(lambda: threading.Thread(target=refresh_worker, daemon=True).start())
    act_autostart = QAction("Автозапуск с Windows", menu, checkable=True)
    act_autostart.setChecked(autostart_enabled())
    act_autostart.toggled.connect(set_autostart)
    act_quit = QAction("Выход", menu)
    act_quit.triggered.connect(on_exit)
    menu.addAction(act_show)
    menu.addAction(act_refresh)
    menu.addAction(act_autostart)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)

    def on_tray_activated(reason):
        if reason == QSystemTrayIcon.Trigger:
            show_popup()
    tray.activated.connect(on_tray_activated)
    tray.show()
    logging.info("tray: иконка запущена")

    threading.Thread(target=refresh_worker, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    watcher_timer = QTimer()
    watcher_timer.timeout.connect(watcher_tick)
    watcher_timer.start(150)
    status_timer = QTimer()
    status_timer.timeout.connect(refresh_status)
    status_timer.start(60_000)  # пик-статус по времени — раз в минуту

    if "--debug-show" in sys.argv:
        # показать на 9 секунд (всё в GUI-потоке)
        QTimer.singleShot(8000, show_popup)
        QTimer.singleShot(17000, hide_popup)

    app.exec()
    logging.info("=== завершение ===")


def on_exit() -> None:
    logging.info("выход из приложения")
    try:
        if tray is not None:
            tray.hide()
    except Exception:
        pass
    os._exit(0)


def main() -> None:
    try:
        _main()
    except Exception:
        log_exc("main: фатальная ошибка")
        raise


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        results = fetch_all()
        cache = merge_cache(results)
        snap = build_snapshot(cache)
        print(json.dumps({
            "ok": not snap["any_error"],
            "glm_status": snap["glm"]["status"], "ds_status": snap["ds"]["status"],
            "glm_peak": snap["glm"].get("peak"), "ds_peak": snap["ds"].get("peak"),
            "warnings": (cache.get("glm", {}).get("warnings", []) + cache.get("ds", {}).get("warnings", [])),
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if not snap["any_error"] else 1)
    else:
        main()
