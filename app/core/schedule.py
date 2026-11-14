"""Пиковые расписания: окна в UTC / UTC+8 → статус и местное время пользователя."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_HOLIDAYS_PATH = Path(__file__).with_name("holidays_cn.json")


def load_holidays() -> dict[str, str]:
    """{'YYYY-MM-DD': 'название праздника'}; при повреждении файла — пустой словарь."""
    try:
        raw = json.loads(_HOLIDAYS_PATH.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}


def _wall(day: date, minutes: int, tz: ZoneInfo) -> datetime:
    """Момент «day HH:MM» в таймзоне tz (как настенное время)."""
    return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=tz)


def peak_state(
    windows: list[tuple[int, int]],
    tz_name: str,
    weekdays: list[int],
    holidays: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Статус пика для расписания.

    windows  : [(start_min, end_min)] — минуты от полуночи в поясе tz_name (end > start)
    weekdays : рабочие дни, 0=понедельник .. 6=воскресенье
    holidays : дни полного не-пика (китайские праздники DeepSeek)

    Возвращает: peak (bool), reason (None|'weekend'|'cn_holiday'|'offpeak_hours'),
                next_change (aware datetime UTC | None), windows_local (['11:00–15:00', ...]).
    """
    tz = ZoneInfo(tz_name)
    now = now or datetime.now(timezone.utc)
    holidays = holidays or {}
    now_tz = now.astimezone(tz)

    in_weekday = now_tz.weekday() in weekdays
    minute = now_tz.hour * 60 + now_tz.minute
    in_window = any(a <= minute < b for a, b in windows)
    is_holiday = now_tz.date().isoformat() in holidays
    peak = in_weekday and in_window and not is_holiday

    if peak:
        reason = None
    elif is_holiday:
        reason = "cn_holiday"
    elif now_tz.weekday() >= 5:
        reason = "weekend"
    else:
        reason = "offpeak_hours"

    # ближайшая граница: при пике — конец окна, иначе — начало
    bound_idx = 1 if peak else 0
    next_change: datetime | None = None
    for d in range(9):
        day = (now_tz + timedelta(days=d)).date()
        if day.isoformat() in holidays:
            continue
        for win in windows:
            inst = _wall(day, win[bound_idx], tz).astimezone(timezone.utc)
            if inst <= now:
                continue
            if inst.astimezone(tz).weekday() not in weekdays:
                continue
            if next_change is None or inst < next_change:
                next_change = inst

    windows_local = []
    for a, b in windows:
        s = _wall(now_tz.date(), a, tz).astimezone().strftime("%H:%M")
        e = _wall(now_tz.date(), b, tz).astimezone().strftime("%H:%M")
        windows_local.append(f"{s}–{e}")

    return {
        "peak": peak,
        "reason": reason,
        "next_change": next_change,
        "windows_local": windows_local,
    }
