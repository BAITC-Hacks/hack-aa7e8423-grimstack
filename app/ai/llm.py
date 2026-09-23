"""LLM-сводка по заказу поставщику.

Модель только пересказывает уже посчитанные цифры: в неё уходит компактная выжимка RunResult,
количества она не считает и не меняет. Живые ответы кэшируются в samples/cache/ по хэшу
выжимки, поэтому без ключа сервис отдаёт сохранённый ответ с пометкой «из кэша».
"""

import hashlib
import json
import logging
import os
from collections import Counter
from pathlib import Path

from app.contracts import OrderLine, RunResult, SummaryResponse, Supplier

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "samples" / "cache"
PROMPT_VERSION = "v2"  # меняется вместе с промптом — старый кэш перестаёт совпадать
TOP_LINES = 8
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

SYSTEM_PROMPT = """Ты аналитик отдела закупа дистрибьютора электротехники.
Тебе дают готовые результаты расчёта заказа одному поставщику в JSON.
Напиши сводку для менеджера закупа на русском: 4–6 коротких пунктов, не больше 120 слов.
1. Итог заказа: сколько позиций, из них срочных, сумма, если она есть.
2. Что срочно и почему: товары из urgent с причиной из explanation.
3. Где наш расчёт сильнее всего расходится с Excel-методом (vs_excel): назови товар, наш заказ (qty) и заказ Excel-метода
(excel_qty) и причину из explanation. Если vs_excel пуст, пункт 3 не пиши совсем.
4. На что обратить внимание перед утверждением: warnings (пересказывай точно, не сокращая смысл) и флаги
(оценочный остаток, разовые заказы, дефицит).
Используй только числа из JSON, ничего не пересчитывай и не придумывай.
Суммы — в тенге (₸), никогда не в рублях. Числа пиши с пробелом между тысячами: 14 800, а не 14,800.
Не предлагай отправить заказ поставщику: его утверждает менеджер."""


def _line_brief(line: OrderLine) -> dict:
    return {"sku": line.sku, "name": line.name, "unit": line.unit, "qty": round(line.final_qty),
            "excel_qty": None if line.baseline_qty is None else round(line.baseline_qty),
            "urgency": line.urgency, "days_of_cover": None if line.days_of_cover is None else round(line.days_of_cover),
            "explanation": line.explanation}


def build_facts(result: RunResult, supplier: Supplier) -> dict | None:
    """Детерминированная выжимка по поставщику: без run_id и времени, чтобы кэш совпадал между запусками."""
    head = next((s for s in result.suppliers if s.supplier == supplier), None)
    if head is None:
        return None
    lines = [l for l in result.lines if l.supplier == supplier]
    rank = {"critical": 0, "high": 1, "planned": 2, "none": 3}
    urgent = sorted((l for l in lines if l.urgency in ("critical", "high")),
                    key=lambda l: (rank[l.urgency], -(l.amount if l.amount is not None else l.final_qty), l.sku))
    excel_run = result.params.method == "baseline"  # сравнивать Excel-метод с самим собой бессмысленно
    vs_excel = [] if excel_run else sorted(
        (l for l in lines if l.baseline_qty is not None and l.final_qty != l.baseline_qty),
        key=lambda l: (-abs(l.final_qty - l.baseline_qty), l.sku))
    return {
        "supplier": supplier,
        "supplier_name": head.supplier_name,
        "data_as_of": result.data_as_of.isoformat(),
        "method": result.params.method,
        "note": "Это расчёт Excel-методом менеджера: сравнение с Excel не применимо." if excel_run
                else "Это наш расчёт; vs_excel — строки, где он сильнее всего отличается от Excel-метода.",
        "currency": "тенге (₸)",
        "lines_count": head.lines_count,
        "critical_count": head.critical_count,
        "total_qty": round(head.total_qty),
        "total_amount": None if head.total_amount is None else round(head.total_amount),
        "flags": dict(sorted(Counter(f for l in lines for f in l.flags).items())),
        "urgent": [_line_brief(l) for l in urgent[:TOP_LINES]],
        "vs_excel": [_line_brief(l) for l in vs_excel[:TOP_LINES]],
        "warnings": [w for w in result.warnings if not _addressed_to_other(
            w, supplier, {s.supplier for s in result.suppliers})],
    }


def _addressed_to_other(warning: str, supplier: Supplier, known_suppliers: set[str]) -> bool:
    """Предупреждения вида «IEK: …» относятся к одному поставщику — в сводку по другому их не несём."""
    return any(warning.startswith(f"{other}:") for other in known_suppliers if other != supplier)


def cache_key(facts: dict) -> str:
    payload = PROMPT_VERSION + json.dumps(facts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _model() -> str:
    """Пустое MODEL= из .env означает «не задано»: пустое имя модели API отклонит."""
    return os.getenv("MODEL") or DEFAULT_MODEL


def _base_url() -> str:
    """Пустое OPENAI_BASE_URL= нельзя отдавать клиенту как None: он сам прочтёт пустую переменную и построит URL без схемы."""
    return os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL


def _complete(system: str, user: str) -> str:
    """Один запрос к OpenAI-совместимому API. Клиент создаётся здесь, а не при импорте: без ключа сервис стартует."""
    from openai import OpenAI

    client = OpenAI(base_url=_base_url(), timeout=30)
    response = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=400,
    )
    return (response.choices[0].message.content or "").strip()


def summary(result: RunResult, supplier: Supplier) -> SummaryResponse | None:
    """None — поставщика нет в результате или нет ни ключа, ни кэша (API → 503)."""
    facts = build_facts(result, supplier)
    if facts is None:
        return None
    path = CACHE_DIR / f"{cache_key(facts)}.json"

    text = _ask(facts) if os.getenv("OPENAI_API_KEY") else None
    if text:
        _save(path, text)  # не удалось сохранить — живой ответ всё равно отдаём
        return SummaryResponse(text=text, cached=False)
    cached = _load(path)
    return SummaryResponse(text=cached, cached=True) if cached else None


def _ask(facts: dict) -> str | None:
    try:
        return _complete(SYSTEM_PROMPT, json.dumps(facts, ensure_ascii=False)) or None
    except Exception:  # сеть, лимиты, неверный ключ — дальше пробуем кэш
        log.warning("LLM недоступна, пробую кэш сводки", exc_info=True)
        return None


def _save(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"text": text, "model": _model()},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        log.warning("Не удалось сохранить сводку в кэш %s", path, exc_info=True)


def _load(path: Path) -> str | None:
    """Текст из кэша; битый или чужой файл (например, после git-конфликта) — как отсутствующий."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))["text"] or None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("Кэш сводки повреждён, игнорирую: %s", path)
        return None
