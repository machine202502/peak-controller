"""Источник: GLM Coding Plan (Z.ai) — markdown-версии официальной документации.

https://docs.z.ai/devpack/overview.md            (лимиты, коэффициенты, пик, скидка)
https://docs.z.ai/devpack/notice/usage-revision.md  (уведомление о правилах — кросс-чек, опционально)
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone

import requests

URL_OVERVIEW = "https://docs.z.ai/devpack/overview.md"
URL_NOTICE = "https://docs.z.ai/devpack/notice/usage-revision.md"
USER_AGENT = "peak-controller/1.0 (local tray widget)"
TIMEOUT = 6          # на одну попытку: нормальный ответ — доли секунды
ATTEMPTS = 3         # docs.z.ai рвёт TLS при частых запросах — пара повторов спасает
BACKOFF = (2, 4)     # паузы между попытками
BUDGET = 10          # секунд на весь источник: лежачий сайт не держит обновление
PAUSE_BETWEEN = 1.5  # docs.z.ai режет частоту запросов — качаем последовательно

EXPECTED_MODELS = ("GLM-5.3", "GLM-5.3-Flash")
EXPECTED_TIERS = {"lite", "pro", "max"}


class ParseError(Exception):
    """Структура страницы незнакома — вероятно, сайт изменился."""


def fetch_text(url: str, attempts: int = ATTEMPTS, deadline: float | None = None) -> str:
    """GET с повторами: docs.z.ai иногда сбрасывает TLS-соединения при частых запросах.

    deadline (time.monotonic) — общий бюджет источника: как только он исчерпан,
    новые попытки не начинаются, чтобы обновление не висело минутами.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    last: Exception | None = None
    for i in range(attempts):
        if deadline is not None and time.monotonic() >= deadline:
            break
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            logging.debug("fetch %s: попытка %d не удалась: %s", url, i + 1, e)
            if i + 1 < attempts and (deadline is None or time.monotonic() < deadline):
                time.sleep(BACKOFF[min(i, len(BACKOFF) - 1)])
    if last is None:
        raise TimeoutError(f"{url}: бюджет обновления исчерпан")
    raise last


def parse_overview(md: str) -> dict:
    text = md.replace("\u2011", "-")  # неразрывный дефис в названиях моделей

    tiers: dict[str, dict[str, int]] = {}
    ratio_warnings: list[str] = []
    for name, h5, week in re.findall(r"\|\s*(Lite|Pro|Max)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|", text):
        h5i, wi = int(h5.replace(",", "")), int(week.replace(",", ""))
        tiers[name.lower()] = {"h5": h5i, "week": wi}
    if set(tiers) != EXPECTED_TIERS:
        raise ParseError(f"тарифы не найдены или неполны: {sorted(tiers)}")
    for key, t in tiers.items():
        if t["week"] != 5 * t["h5"]:
            ratio_warnings.append(f"тариф {key}: недельные кредиты ≠ 5 × пятичасовые")

    models = []
    for name, mi, mc, mo in re.findall(
        r"<td>\s*(GLM-[\w.\-]+).*?</td>\s*<td>\s*([\d.]+)\s*</td>\s*<td>\s*([\d.]+)\s*</td>\s*<td>\s*([\d.]+)\s*</td>",
        text,
        re.S,
    ):
        models.append({"name": name, "in": float(mi), "cached": float(mc), "out": float(mo)})
    if not models:
        raise ParseError("таблица коэффициентов моделей не найдена")

    m = re.search(r"Peak hours\*\*:\s*Monday to Friday,\s*(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})", text)
    if not m:
        raise ParseError("не найдена строка пиковых часов")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    windows = [[h1 * 60 + m1, h2 * 60 + m2]]
    if "Monday to Friday" not in text or "UTC+8" not in text:
        raise ParseError("не найдены условия расписания (будни / UTC+8)")

    pm = re.search(r"charged at (\d{1,2})% of the standard credit rate", text)
    off_pct = int(pm.group(1)) if pm else None
    if not off_pct or not 30 <= off_pct <= 70:
        raise ParseError(f"неожиданный процент не-пик списания: {off_pct}")

    # оценка токенов в неделю (строка «95%», модель GLM-5.3): Lite/Pro/Max
    estimates: dict[str, str] = {}
    em = re.search(
        r"\|\s*95%\s*\|\s*GLM-5\.3\s*\|\s*([\d,]+)\s*[–—-]\s*([\d,]+)\s*\|\s*([\d,]+)\s*[–—-]\s*([\d,]+)\s*\|\s*([\d,]+)\s*[–—-]\s*([\d,]+)\s*\|",
        text,
    )
    if em:
        vals = [f"{em.group(i)}–{em.group(i + 1)}" for i in (1, 3, 5)]
        estimates = {"lite": vals[0], "pro": vals[1], "max": vals[2]}

    warnings = ratio_warnings
    names = {mo["name"] for mo in models}
    if not set(EXPECTED_MODELS) <= names:
        warnings.append("набор моделей изменился: " + ", ".join(sorted(names)))

    return {
        "tiers": tiers,
        "models": models,
        "peak_windows_sgt": windows,
        "tz": "Asia/Singapore",
        "offpeak_pct": off_pct,
        "token_estimates": estimates,
        "warnings": warnings,
    }


def refresh() -> dict:
    """Свежий снимок источника. overview обязателен, notice — опциональный кросс-чек."""
    deadline = time.monotonic() + BUDGET
    overview_md = fetch_text(URL_OVERVIEW, deadline=deadline)
    data = parse_overview(overview_md)
    hashes = {"overview": hashlib.sha256(overview_md.encode("utf-8")).hexdigest()}
    warnings = list(data.get("warnings", []))

    time.sleep(PAUSE_BETWEEN)
    try:
        notice_md = fetch_text(URL_NOTICE, deadline=deadline)
        hashes["notice"] = hashlib.sha256(notice_md.encode("utf-8")).hexdigest()
        t = notice_md.replace("\u2011", "-")
        mm = re.search(r"Peak hours[^.]*?(\d{1,2}):(\d{2})\s*[–—-]\s*(\d{1,2}):(\d{2})", t)
        if mm:
            cross = [[int(mm.group(1)) * 60 + int(mm.group(2)), int(mm.group(3)) * 60 + int(mm.group(4))]]
            if cross != data["peak_windows_sgt"]:
                warnings.append("страница-уведомление описывает другое окно пика")
    except Exception:
        hashes["notice"] = None
        warnings.append("страница уведомлений недоступна — кросс-чек пропущен")

    return {"data": data, "hashes": hashes, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


if __name__ == "__main__":  # ручная проверка: py app/sources/glm.py
    import json

    print(json.dumps(refresh(), ensure_ascii=False, indent=2))
