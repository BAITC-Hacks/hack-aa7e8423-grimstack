"""Ядро расчёта заказов. Владелец — ядро (А).

Сигнатуры финальные, заморожены. Реализация — в app/engine/pipeline.py.
"""

from app.contracts import Meta, RunParams, RunResult, SkuHistory, Supplier
from app.engine import pipeline


def run(data, params: RunParams) -> RunResult:
    """params.method выбирает analyze (наш метод) или baseline (Excel-метод)."""
    return pipeline.run(data, params)


def history(data, supplier: Supplier, sku: str, params: RunParams) -> SkuHistory:
    """KeyError, если (supplier, sku) нет в датасете."""
    return pipeline.history(data, supplier, sku, params)


def meta(data) -> Meta:
    return pipeline.meta(data)
