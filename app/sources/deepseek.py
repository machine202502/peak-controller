"""Источник: официальный прайс DeepSeek API — markdown-версия страницы документации.

https://api-docs.deepseek.com/quick_start/pricing.md  (цены, окна пика)
https://api-docs.deepseek.com/updates.md              (changelog, только hash для детекции правок)
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone

import requests

URL_PRICING = "https://api-docs.deepseek.com/quick_start/pricing.md"
URL_UPDATES = "https://api-docs.deepseek.com/updates.md"
USER_AGENT = "peak-controller/1.0 (local tray widget)"
TIMEOUT = 25

EXPECTED_ALIASES = {"deepseek-flash", "deepseek-v4-pro"}
_METRICS = (("CACHE HIT", "input_cache_hit"), ("CACHE MISS", "input"), ("OUTPUT TOKENS", "output"))


class ParseError(Exception):
    """Структура страницы незнакома — вероятно, сайт изменился."""


def fetch_text(url: str, attempts: int = 3) -> str:
    """GET с повторами на случай сетевых сбоев."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            if i + 1 < attempts:
                time.sleep(2 * (i + 1))
    raise last  # type: ignore[misc]


def parse_pricing(md: str) -> dict:
    # псевдонимы моделей — из шапки таблицы Model Details
    aliases: list[str] = []
    for line in md.splitlines():
        if line.startswith("| MODEL"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            for c in cells:
                for a in re.findall(r"deepseek-[\w-]+", c):
                    if a not in aliases:
                        aliases.append(a)
            if aliases:
                break
    if len(aliases) != 2:
        raise ParseError(f"ожидались 2 псевдонима моделей, найдено: {aliases}")

    versions: list[str] = []
    for line in md.splitlines():
        if "MODEL VERSION" in line:
            versions = re.findall(r"DeepSeek-[A-Za-z0-9.\-]+", line)
            break

    # цены: | PRICING(2) | 1M INPUT TOKENS / (CACHE HIT) | OFF-PEAK | $0.003 | $0.022 |
    prices: dict[int, dict[str, dict[str, float]]] = {}
    rows = 0
    for line in md.splitlines():
        if not line.startswith("| PRICING"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        metric = next((v for key, v in _METRICS if any(key in c for c in cells)), None)
        direction = next(("offpeak" if c == "OFF-PEAK" else "peak" for c in cells if c in ("OFF-PEAK", "PEAK")), None)
        if metric is None or direction is None:
            continue
        nums = [c for c in cells if c.startswith("$")]
        if len(nums) != 2:
            raise ParseError(f"строка цены без двух значений: {line[:80]!r}")
        for idx, raw in enumerate(nums):
            prices.setdefault(idx, {}).setdefault(metric, {})[direction] = float(raw[1:])
        rows += 1
    if rows != 6:
        raise ParseError(f"ожидалось 6 строк цен, найдено {rows}")

    m = re.search(
        r"Peak hours are\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s+and\s+"
        r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*UTC",
        md,
    )
    if not m:
        raise ParseError("не найдена строка пиковых часов")
    g = [int(x) for x in m.groups()]
    windows = [[g[0] * 60 + g[1], g[2] * 60 + g[3]], [g[4] * 60 + g[5], g[6] * 60 + g[7]]]
    if "Monday through Friday" not in md or "excluding Chinese public holidays" not in md:
        raise ParseError("не найдены условия расписания (будни / китайские праздники)")

    warnings: list[str] = []
    if set(aliases) != EXPECTED_ALIASES:
        warnings.append("состав моделей изменился: " + ", ".join(aliases))

    for i in range(2):
        for metric, pair in prices.get(i, {}).items():
            off, peak = pair.get("offpeak"), pair.get("peak")
            if off is None or peak is None:
                raise ParseError(f"нет пары цен не-пик/пик: {aliases[i]} {metric}")
            if abs(off * 2 - peak) > max(peak, off) * 1e-6:
                warnings.append(f"{aliases[i]}/{metric}: не-пик ≠ пик/2 — правила скидки изменились?")
            if not (0 < off < 100 and 0 < peak < 100):
                raise ParseError(f"цена вне разумных границ: {aliases[i]} {metric} = {off}/{peak}")

    models = [
        {"alias": aliases[i], "version": versions[i] if i < len(versions) else "", "prices": prices[i]}
        for i in range(2)
    ]
    return {
        "models": models,
        "peak_windows_utc": windows,
        "weekend_offpeak": True,
        "cn_holidays_offpeak": True,
        "warnings": warnings,
    }


def refresh() -> dict:
    """Свежий снимок источника. Сетевые/парсинговые ошибки пробрасываются наверх."""
    pricing_md = fetch_text(URL_PRICING)
    data = parse_pricing(pricing_md)
    hashes = {"pricing": hashlib.sha256(pricing_md.encode("utf-8")).hexdigest()}
    try:
        updates_md = fetch_text(URL_UPDATES)
        hashes["updates"] = hashlib.sha256(updates_md.encode("utf-8")).hexdigest()
    except Exception:
        hashes["updates"] = None  # changelog вторичен, его недоступность не ломает источник
    return {"data": data, "hashes": hashes, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


if __name__ == "__main__":  # ручная проверка: py app/sources/deepseek.py
    snap = refresh()
    import json

    print(json.dumps(snap, ensure_ascii=False, indent=2))
    time.sleep(0)  # для единообразия импортов
