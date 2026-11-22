"""DeepSeek dashboard composition with a deterministic offline fallback.

The model receives the catalog and the current dashboard, and returns a strict
JSON spec. Every field is validated against the archive before it is applied;
the model never computes a number that the server has not calculated.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, timedelta

import requests

from .data import PARAMETERS, STATION_PARAMS, STATIONS

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

MONTHS = [
    ("январ", 1),
    ("феврал", 2),
    ("март", 3),
    ("апрел", 4),
    ("май", 5),
    ("мая", 5),
    ("мае", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("сентябр", 9),
    ("октябр", 10),
    ("ноябр", 11),
    ("декабр", 12),
]

SYSTEM = """Ты — аналитик данных Центрально-Лесного заповедника. Твоя задача — превратить
запрос сотрудника в спецификацию дашборда. Верни только JSON без markdown.

Допустимые поля:
{
  "station": "AME60" | "KR1" | "KR2",
  "parameter": один из параметров этой станции,
  "start": "YYYY-MM-DD",
  "end": "YYYY-MM-DD",
  "granularity": "day" | "month" | "year",
  "baseline": true | false,
  "annotations": true | false,
  "risk_focus": "fire" | "flood" | "heat" | "drought" | null,
  "title": "короткий заголовок на русском",
  "explanation": "1-2 предложения, что именно показано"
}

Правила:
- Не придумывай значения и не считай статистику. Только выбери срез.
- Не объединяй АМЕ-60 и Сокол М1: это разные приборы и точки.
- Если запрос про давление — только KR1 или KR2. Если про почву — только AME60.
- Если период не назван, сохрани текущий период. Если станция не названа, сохрани текущую.
- Для «пожара», «паводка», «жары», «засухи» поставь risk_focus и выбери температуру/влажность/ветер.
- Ответ строго по схеме, без пояснений вокруг JSON."""


class AIClient:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.runtime_key: str | None = None
        self.runtime_base: str | None = None
        self.runtime_model: str | None = None

    @property
    def key(self) -> str | None:
        return self.runtime_key or os.environ.get("DEEPSEEK_API_KEY") or None

    @property
    def base(self) -> str:
        return (self.runtime_base or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE).rstrip("/")

    @property
    def model(self) -> str:
        return self.runtime_model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL

    def status(self) -> dict:
        key = self.key
        return {
            "available": bool(key),
            "model": self.model,
            "base": self.base,
            "source": "runtime" if self.runtime_key else ("env" if os.environ.get("DEEPSEEK_API_KEY") else "none"),
            "key_hint": (key[:4] + "…" + key[-4:]) if key and len(key) > 8 else ("настроен" if key else None),
            "fallback": "Локальный разбор запроса, если DeepSeek недоступен",
        }

    def configure(self, key: str | None, base: str | None = None, model: str | None = None) -> dict:
        with self.lock:
            self.runtime_key = (key or "").strip() or None
            self.runtime_base = (base or "").strip().rstrip("/") or None
            self.runtime_model = (model or "").strip() or None
        return self.status()

    def _endpoint(self) -> str:
        base = self.base
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/chat/completions"

    def _chat(self, messages: list[dict], json_mode: bool = True, timeout: int = 35) -> str:
        key = self.key
        if not key:
            raise RuntimeError("DeepSeek API key не настроен")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.15,
            "max_tokens": 1400,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = requests.post(
            self._endpoint(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"DeepSeek HTTP {response.status_code}: {response.text[:300]}")
        body = response.json()
        return body["choices"][0]["message"]["content"]

    def compose(self, prompt: str, current: dict, catalog: dict) -> dict:
        local = local_compose(prompt, current, catalog)
        if not self.key:
            local["source"] = "local"
            local["explanation"] = local["explanation"] + " DeepSeek не настроен: добавьте ключ в настройках."
            return local
        compact = {
            "stations": [
                {
                    "code": s["code"],
                    "label": s["label"],
                    "place": s["place"],
                    "first": s["first"],
                    "last": s["last"],
                    "parameters": [p["code"] for p in s["parameters"]],
                }
                for s in catalog["stations"]
            ],
            "current": current,
            "request": prompt,
        }
        try:
            content = self._chat(
                [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
                ]
            )
            parsed = _extract_json(content)
            if isinstance(parsed.get("spec"), dict):
                parsed = parsed["spec"]
            elif isinstance(parsed.get("dashboard"), dict):
                parsed = parsed["dashboard"]
            if "from" in parsed and "start" not in parsed:
                parsed["start"] = parsed["from"]
            if "to" in parsed and "end" not in parsed:
                parsed["end"] = parsed["to"]
            spec = validate_spec(parsed, current, catalog)
            spec["explanation"] = str(parsed.get("explanation") or spec["title"])
            return {"spec": spec, "explanation": spec["explanation"], "source": "deepseek", "model": self.model}
        except Exception as exc:  # noqa: BLE001 - surface the reason and keep working
            local["source"] = "local"
            local["error"] = str(exc)[:300]
            local["explanation"] = local["explanation"] + f" DeepSeek недоступен: {str(exc)[:120]}."
            return local

    def narrative(self, context: dict) -> dict:
        if not self.key:
            return {"text": None, "source": "local"}
        system = (
            "Ты — аналитик, который пишет краткий отчёт для руководителя заповедника. "
            "Используй только факты из JSON. Не придумывай числа, причины и прогнозы. "
            "Пиши по-русски, 4–6 предложений: что произошло, чем это важно, что сделать и какое ограничение у данных. "
            "Верни JSON {\"text\": \"...\"}."
        )
        try:
            content = self._chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)},
                ],
                timeout=30,
            )
            parsed = _extract_json(content)
            text = str(parsed.get("text", "")).strip()
            if not text:
                raise RuntimeError("пустой ответ")
            return {"text": text, "source": "deepseek", "model": self.model}
        except Exception as exc:  # noqa: BLE001
            return {"text": None, "source": "local", "error": str(exc)[:300]}


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("ответ не является JSON-объектом")
    return value


def _station_from(text: str, current: str) -> str:
    if re.search(r"кр\s*1|kr\s*1|полян", text):
        return "KR1"
    if re.search(r"кр\s*2|kr\s*2|(?<![а-я])поле(?![а-я])", text):
        return "KR2"
    if re.search(r"аме|ame\s*60|аме-60", text):
        return "AME60"
    return current


def _parameter_from(text: str, station: str, current: str) -> str:
    allowed = STATION_PARAMS[station]
    if re.search(r"давлен|pressure", text) and "pressure_hpa" in allowed:
        return "pressure_hpa"
    if re.search(r"порыв|gust", text):
        return "wind_gust"
    if re.search(r"направлен|wind.?dir|роза", text):
        return "wind_dir"
    if re.search(r"влажност|rh\b|humidity", text):
        return "rh"
    if re.search(r"ветер|ветра|wind", text):
        return "wind_speed"
    if re.search(r"почв", text):
        depth_match = re.search(r"(5|10|20)\s*см", text)
        if depth_match:
            candidate = f"soil_{depth_match.group(1).zfill(2)}"
            if candidate in allowed:
                return candidate
        if "soil_10" in allowed:
            return "soil_10"
    if re.search(r"температур|t_air|тепл", text):
        return "t_air"
    return current if current in allowed else allowed[0]


def _range_from(text: str, current: dict, first: str, last: str) -> tuple[str, str]:
    first_date = date.fromisoformat(first)
    last_date = date.fromisoformat(last)
    years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", text)]
    start = date.fromisoformat(current["start"])
    end = date.fromisoformat(current["end"])
    month = None
    for stem, number in MONTHS:
        if stem in text:
            month = number
            break
    if years:
        year = years[0]
        if month:
            start = date(year, month, 1)
            end = date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)
        else:
            start = date(year, 1, 1)
            end = date(year, 12, 31)
    elif month:
        year = start.year if start.year >= first_date.year else first_date.year
        start = date(year, month, 1)
        end = date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)
    if re.search(r"последн\w*\s+(\d+)\s+дн", text):
        days = int(re.search(r"последн\w*\s+(\d+)\s+дн", text).group(1))
        end = last_date
        start = max(first_date, end - timedelta(days=days - 1))
    elif re.search(r"последн\w*\s+(\d+)\s+мес", text):
        months = int(re.search(r"последн\w*\s+(\d+)\s+мес", text).group(1))
        end = last_date
        year = end.year
        month_index = end.month - months + 1
        while month_index <= 0:
            month_index += 12
            year -= 1
        start = max(first_date, date(year, month_index, 1))
    if not month and "последн" not in text:
        season_year = years[0] if years else last_date.year
        if re.search(r"летом|летн|лето", text):
            start, end = date(season_year, 6, 1), date(season_year, 8, 31)
        elif re.search(r"зим|зимн", text):
            start, end = date(season_year - 1, 12, 1), date(season_year, 2, 28)
        elif re.search(r"весн|весен", text):
            start, end = date(season_year, 3, 1), date(season_year, 5, 31)
        elif re.search(r"осен|осенн", text):
            start, end = date(season_year, 9, 1), date(season_year, 11, 30)
    if "весь ряд" in text or "всю истори" in text or "за все годы" in text:
        start, end = first_date, last_date
    start = max(first_date, min(start, last_date))
    end = max(first_date, min(end, last_date))
    if start > end:
        start, end = first_date, last_date
    return start.isoformat(), end.isoformat()


def local_compose(prompt: str, current: dict, catalog: dict) -> dict:
    text = prompt.lower().replace("ё", "е")
    station = _station_from(text, current.get("station", "KR1"))
    station_info = next((s for s in catalog["stations"] if s["code"] == station), None)
    if station_info is None:
        station = current.get("station", "KR1")
        station_info = next(s for s in catalog["stations"] if s["code"] == station)
    parameter = _parameter_from(text, station, current.get("parameter", "t_air"))
    if parameter not in STATION_PARAMS[station]:
        parameter = STATION_PARAMS[station][0]
    start, end = _range_from(text, current, station_info["first"], station_info["last"])
    granularity = current.get("granularity", "month")
    if re.search(r"по\s+дн|суточн|день|дням|daily", text):
        granularity = "day"
    if re.search(r"по\s+мес|месяч|monthly", text):
        granularity = "month"
    if re.search(r"по\s+год|годов|yearly", text):
        granularity = "year"
    focus = None
    for key, pattern in (
        ("fire", r"пожар"),
        ("flood", r"павод|наводнен|подтоп"),
        ("heat", r"жар|теплов"),
        ("drought", r"засух|сухост"),
    ):
        if re.search(pattern, text):
            focus = key
            break
    annotations = "без размет" not in text and "скрыть размет" not in text
    baseline = "без баз" not in text and "без сравнен" not in text
    title = f"{station_info['label']} · {PARAMETERS[parameter]['label'].lower()}"
    if start[:4] == end[:4]:
        title += f" · {start[:4]}"
    else:
        title += f" · {start[:4]}–{end[:4]}"
    spec = {
        "station": station,
        "parameter": parameter,
        "start": start,
        "end": end,
        "granularity": granularity,
        "baseline": baseline,
        "annotations": annotations,
        "risk_focus": focus,
        "title": title,
    }
    explanation = f"Ряд: {PARAMETERS[parameter]['label'].lower()} · {station_info['label']} · {start} — {end}, детализация «{ {'day': 'день', 'month': 'месяц', 'year': 'год'}[granularity] }»."
    if focus:
        explanation += f" Акцент на риске: { {'fire': 'пожарная опасность', 'flood': 'подтопление', 'heat': 'жара', 'drought': 'засуха'}[focus] }."
    return {"spec": spec, "explanation": explanation, "source": "local"}


def validate_spec(parsed: dict, current: dict, catalog: dict) -> dict:
    station = str(parsed.get("station") or current.get("station") or "KR1").upper()
    if station not in STATIONS:
        station = current.get("station", "KR1")
    station_info = next(s for s in catalog["stations"] if s["code"] == station)
    parameter = str(parsed.get("parameter") or current.get("parameter") or "t_air")
    if parameter not in STATION_PARAMS[station]:
        parameter = STATION_PARAMS[station][0]
    first = date.fromisoformat(station_info["first"])
    last = date.fromisoformat(station_info["last"])
    try:
        start = date.fromisoformat(str(parsed.get("start") or current.get("start")))
    except ValueError:
        start = first
    try:
        end = date.fromisoformat(str(parsed.get("end") or current.get("end")))
    except ValueError:
        end = last
    start = max(first, min(start, last))
    end = max(first, min(end, last))
    if start > end:
        start, end = first, last
    granularity = str(parsed.get("granularity") or current.get("granularity") or "month")
    if granularity not in {"day", "month", "year"}:
        granularity = "month"
    focus = parsed.get("risk_focus")
    if focus not in {"fire", "flood", "heat", "drought"}:
        focus = None
    return {
        "station": station,
        "parameter": parameter,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "granularity": granularity,
        "baseline": bool(parsed.get("baseline", current.get("baseline", True))),
        "annotations": bool(parsed.get("annotations", current.get("annotations", True))),
        "risk_focus": focus,
        "title": str(parsed.get("title") or f"{station_info['label']} · {PARAMETERS[parameter]['label'].lower()}")[:120],
    }


AI = AIClient()
