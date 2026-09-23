"""Моки в contracts/ обязаны совпадать с app/contracts.py — на них работают API и фронт."""

import json
from pathlib import Path

from app.contracts import Meta, RunResult, SkuHistory

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_sample_run_matches_contract():
    run = RunResult.model_validate(load("sample_run.json"))
    assert run.lines, "мок должен содержать строки"
    by_supplier = {s.supplier: s for s in run.suppliers}
    for line in run.lines:
        assert line.supplier in by_supplier
        assert line.line_id == f"{line.supplier}:{line.sku}"
        assert line.explanation and line.components
        assert line.final_qty % line.moq == 0
        waterfall = sum(c.value for c in line.components if c.kind == "qty")
        assert abs(waterfall - line.recommended_qty) < 1e-6, line.line_id
        if line.unit_cost is not None:
            assert line.amount == round(line.final_qty * line.unit_cost, 2)
    for s in run.suppliers:
        own = [l for l in run.lines if l.supplier == s.supplier]
        assert s.lines_count == len(own)
        assert s.total_qty == sum(l.final_qty for l in own)


def test_sample_history_matches_contract():
    h = SkuHistory.model_validate(load("sample_sku_history.json"))
    n = len(h.months)
    assert len(h.raw) == len(h.cleaned) == len(h.restored) == len(h.stockout) == n
    assert len(h.forecast) == len(h.forecast_months)


def test_sample_meta_matches_contract():
    Meta.model_validate(load("sample_meta.json"))
