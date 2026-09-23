"""Ядро расчёта заказов. Владелец — ядро (А).

Сигнатуры финальные. Пока тела отдают моки contracts/*.json; настоящий расчёт
заменит их без изменения интерфейса.
"""

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.contracts import Meta, RunParams, RunResult, SkuHistory, Supplier

_SAMPLES = Path(__file__).resolve().parents[2] / "contracts"


def _sample(name: str) -> dict:
    return json.loads((_SAMPLES / name).read_text(encoding="utf-8"))


def run(data, params: RunParams) -> RunResult:
    """params.method выбирает analyze (наш метод) или baseline (Excel-метод)."""
    result = RunResult.model_validate(_sample("sample_run.json"))
    keep = lambda s: params.supplier in (None, s)  # noqa: E731
    return result.model_copy(update={
        "run_id": uuid4().hex[:12],
        "created_at": datetime.now(),
        "params": params,
        "lines": [l for l in result.lines if keep(l.supplier)],
        "suppliers": [s for s in result.suppliers if keep(s.supplier)],
    })


def history(data, supplier: Supplier, sku: str, params: RunParams) -> SkuHistory:
    return SkuHistory.model_validate(_sample("sample_sku_history.json") | {"supplier": supplier, "sku": sku})


def meta(data) -> Meta:
    return Meta.model_validate(_sample("sample_meta.json"))
