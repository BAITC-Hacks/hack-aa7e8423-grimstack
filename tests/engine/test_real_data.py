"""Задача 6 плана: интеграция ingest+engine на реальных выгрузках (docs/design.md §5, §9)."""

import math
import time

import pandas as pd
import pytest

from app import engine, ingest
from app.contracts import RunParams

_TIMING: dict[str, float] = {}


@pytest.fixture(scope="module")
def ds():
    t0 = time.perf_counter()
    data = ingest.load_default()
    _TIMING["load_default"] = time.perf_counter() - t0
    return data


@pytest.fixture(scope="module")
def result(ds):
    t0 = time.perf_counter()
    r = engine.run(ds, RunParams())
    _TIMING["run_analyze"] = time.perf_counter() - t0
    return r


def _is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def test_load_default_under_budget(ds):
    assert _TIMING["load_default"] < 40, f"load_default занял {_TIMING['load_default']:.1f}с (лимит 40с)"


def test_run_under_budget(result):
    assert _TIMING["run_analyze"] < 15, f"run(analyze) занял {_TIMING['run_analyze']:.1f}с (лимит 15с)"


def test_lines_are_well_formed(result):
    assert result.lines
    required_numeric = ("stock_free", "in_transit", "forecast_monthly", "safety_stock",
                         "order_up_to", "moq", "recommended_qty", "final_qty")
    optional_numeric = ("baseline_qty", "days_of_cover", "unit_cost", "amount")
    for l in result.lines:
        assert l.recommended_qty >= 0, l.line_id
        assert l.final_qty >= 0, l.line_id
        assert l.moq > 0, l.line_id
        remainder = l.recommended_qty % l.moq
        assert remainder < 1e-6 or (l.moq - remainder) < 1e-6, f"{l.line_id}: {l.recommended_qty} не кратно {l.moq}"
        for field in required_numeric:
            assert not _is_nan(getattr(l, field)), f"{l.line_id}.{field} = NaN"
        for field in optional_numeric:
            v = getattr(l, field)
            assert v is None or not _is_nan(v), f"{l.line_id}.{field} = NaN"
        assert l.explanation.strip(), l.line_id
        assert l.components, l.line_id
        waterfall = sum(c.value for c in l.components if c.kind == "qty")
        assert abs(waterfall - l.recommended_qty) < 1e-6, f"{l.line_id}: водопад {waterfall} != {l.recommended_qty}"


def test_both_suppliers_present(result):
    assert {l.supplier for l in result.lines} == {"IEK", "SE"}


def test_baseline_method_runs(ds):
    r = engine.run(ds, RunParams(method="baseline"))
    assert r.lines
    assert {l.supplier for l in r.lines}


# --- демо-SKU (docs/design.md §5) ------------------------------------------------------------


def test_loop_oneoff_event_present(ds):
    # 130200305_ «Петля LOOP»: 210 000 шт одной накладной 09.06.2025; в помесячном файле
    # этого SKU нет (design.md §5), поэтому «разовость» видна только по sales_tx —
    # сегмент SKU «none» (нет истории), в engine.run строка не попадает, это ожидаемо.
    tx = ds.sales_tx
    sub = tx[(tx["supplier"] == "IEK") & (tx["sku"] == "130200305_")]
    assert (sub["qty"] == 210000).any()


def test_stockout_restored_for_demo_sku(ds):
    # 010300096_: май 2025 разовая строка → июнь начинается с нулевого остатка (stockout),
    # восстановленный спрос должен превысить очищенный хотя бы в одном из месяцев окна.
    h = engine.history(ds, "IEK", "010300096_", RunParams())
    idx = {m: i for i, m in enumerate(h.months)}
    window = [idx[m] for m in ("2025-05", "2025-06", "2025-07") if m in idx]
    assert window, "демо-месяцы вне окна закрытой истории"
    assert any(h.stockout[i] is not None for i in window)
    assert any(h.restored[i] > h.cleaned[i] for i in window)


def test_seasonal_forecast_for_demo_sku(ds):
    # 130300792_ Труба Ø50: октябрь — сезонный пик группы 1303, декабрь — спад.
    h = engine.history(ds, "IEK", "130300792_", RunParams())
    fc = dict(zip(h.forecast_months, h.forecast))
    assert fc["2026-10"] > fc["2026-12"]


def test_trend_up_for_demo_sku(result, ds):
    # SE 030200201_ IMT35101: устойчивый рост 2024→2025→2026 (design.md §5).
    line = next((l for l in result.lines if l.supplier == "SE" and l.sku == "030200201_"), None)
    if line is not None:
        assert "trend_up" in line.flags
    else:  # запасной путь, если сегментация не даёт строку в заказе
        h = engine.history(ds, "SE", "030200201_", RunParams())
        avg_last12 = sum(h.restored[-12:]) / 12
        assert h.forecast[0] > avg_last12
