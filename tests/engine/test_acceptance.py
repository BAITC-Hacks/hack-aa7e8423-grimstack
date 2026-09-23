"""Приёмка must-have кейса (docs/design.md §9) на синтетических данных."""

from datetime import date

import pandas as pd

from app import engine
from app.contracts import OrderLine, RunParams
from app.ingest.dataset import concat
from tests.engine.factory import add_oneoff, make_dataset, noisy, seasonal


def line(ds, sku="A", **params) -> OrderLine | None:
    result = engine.run(ds, RunParams(**params))
    return next((l for l in result.lines if l.sku == sku), None)


def rec(ds, sku="A", **params) -> float:
    found = line(ds, sku, **params)
    return found.recommended_qty if found else 0.0


# MH1: расчёт учитывает все источники — изменение любого меняет результат
def test_in_transit_reduces_order():
    assert rec(make_dataset({"A": noisy(100)}, in_transit=30)) < rec(make_dataset({"A": noisy(100)}))


def test_every_source_moves_the_result():
    base = rec(make_dataset({"A": noisy(100)}))
    assert base > 0
    assert rec(make_dataset({"A": noisy(100)}, stock_now=120)) < base  # остатки
    assert rec(make_dataset({"A": noisy(200)})) > base  # история продаж
    assert rec(make_dataset({"A": noisy(100)}), growth_pct=30) > base  # прогноз прироста
    assert rec(make_dataset({"A": noisy(100)}, category="A")) > rec(make_dataset({"A": noisy(100)}, category="C"))


# MH2: сезонность и устойчивый рост, а не среднее по истории
PEAK_IN_OCTOBER = [0.6, 0.6, 0.8, 1.0, 1.1, 1.2, 1.3, 1.3, 1.2, 1.5, 0.9, 0.5]


def test_seasonal_forecast_follows_pattern():
    ds = make_dataset({"A": seasonal(100, PEAK_IN_OCTOBER)})
    h = engine.history(ds, "IEK", "A", RunParams())
    forecast = dict(zip(h.forecast_months, h.forecast))
    assert forecast["2026-10"] > 1.5 * forecast["2026-12"]
    flat_mean = sum(h.restored[-12:]) / 12
    assert abs(forecast["2026-10"] - flat_mean) / flat_mean > 0.2
    assert "seasonal" in line(ds).flags


def test_steady_growth_lifts_forecast():
    growing = [100 * 1.02**i for i in range(33)]  # ≈ +27 % год к году
    ds = make_dataset({"A": growing})
    found = line(ds)
    last12 = sum(growing[20:32]) / 12
    assert found.forecast_monthly > 1.05 * last12
    assert "trend_up" in found.flags


# MH3: упущенный спрос в stockout компенсируется
def stockout_case():
    sales, stock = noisy(100), [1000.0] * 33
    for i in (26, 27, 28):  # 2026-03…2026-05: товара не было, продажи провалились
        sales[i], stock[i] = 5.0, 0.0
    with_stockout = make_dataset({"A": sales}, stock={"A": stock})
    same_sales_in_stock = make_dataset({"A": sales}, stock={"A": [1000.0] * 33})
    return with_stockout, same_sales_in_stock


def test_stockout_raises_need_over_raw_sales():
    with_stockout, raw = stockout_case()
    assert rec(with_stockout) > rec(raw)
    assert "stockout_restored" in line(with_stockout).flags


def test_history_shows_restored_demand():
    with_stockout, _ = stockout_case()
    h = engine.history(with_stockout, "IEK", "A", RunParams())
    march = h.months.index("2026-03")
    assert h.stockout[march] == "full"
    assert h.restored[march] > h.raw[march]


# MH4: разовый крупный заказ не раздувает регулярную потребность
def test_oneoff_order_does_not_inflate_regular_need():
    regular = make_dataset({"A": noisy(100)})
    spiked = make_dataset({"A": noisy(100)})
    add_oneoff(spiked, "A", date(2026, 6, 10), 2000)  # ×100 от типичной строки
    before, after = rec(regular), line(spiked)
    assert abs(after.recommended_qty - before) <= 0.10 * before
    assert "oneoff_excluded" in after.flags


def test_baseline_is_inflated_by_the_same_oneoff():
    regular = make_dataset({"A": noisy(100)})
    spiked = make_dataset({"A": noisy(100)})
    add_oneoff(spiked, "A", date(2026, 6, 10), 2000)
    assert rec(spiked, method="baseline") > 1.5 * rec(regular, method="baseline")


# MH5: список по поставщикам, обоснование у каждой строки
def two_suppliers():
    iek = make_dataset({"A": noisy(100), "B": noisy(40)}, supplier="IEK", moq=10)
    se = make_dataset({"C": noisy(300)}, supplier="SE", moq=12, category="Кат. 1", unit_cost=100.0)
    return concat([iek, se])


def test_lines_grouped_by_supplier_with_explanations():
    result = engine.run(two_suppliers(), RunParams())
    assert {l.supplier for l in result.lines} == {"IEK", "SE"}
    for s in result.suppliers:
        own = [l for l in result.lines if l.supplier == s.supplier]
        assert s.lines_count == len(own)
    for l in result.lines:
        assert l.explanation.strip() and l.components
        waterfall = sum(c.value for c in l.components if c.kind == "qty")
        assert abs(waterfall - l.recommended_qty) < 1e-6, l.line_id
        assert l.final_qty % l.moq == 0
        if l.supplier == "SE":
            assert l.amount == round(l.final_qty * 100.0, 2)


def test_supplier_filter():
    result = engine.run(two_suppliers(), RunParams(supplier="SE"))
    assert result.lines and {l.supplier for l in result.lines} == {"SE"}


def test_no_order_when_stock_covers_horizon():
    assert line(make_dataset({"A": noisy(100)}, stock_now=10_000)) is None or rec(make_dataset({"A": noisy(100)}, stock_now=10_000)) == 0


def test_months_are_periods():
    ds = make_dataset({"A": noisy(100)})
    assert isinstance(ds.sales_monthly.columns[0], pd.Period)


def test_waterfall_is_exact_with_fractional_moq():
    for level in (10.3, 17.7, 23.1, 41.9):
        found = line(make_dataset({"A": noisy(level)}, moq=0.25, stock_now=0))
        waterfall = sum(c.value for c in found.components if c.kind == "qty")
        assert abs(waterfall - found.recommended_qty) < 1e-6, (level, waterfall, found.recommended_qty)


def test_intermittent_floor_is_its_own_component():
    rare = [30.0 if i % 3 == 0 else 0.0 for i in range(33)]  # продажа раз в квартал
    found = line(make_dataset({"A": rare}, stock_now=0, tx_lines=1), service_level=0.6)
    keys = {c.key: c.value for c in found.components}
    assert "intermittent" in found.flags
    assert keys.get("min_order_floor", 0) > 0
    assert abs(keys["moq_rounding"]) < found.moq


def test_explanation_names_the_forecast_month():
    found = line(make_dataset({"A": seasonal(100, PEAK_IN_OCTOBER)}))
    assert found.explanation.startswith("Октябрь — сезонный пик"), found.explanation


# задача 1 плана: ложная сезонность — у intermittent/lumpy сезонный размах по группе не держится
# статистически, флаг и причина не должны на него полагаться
def test_intermittent_demand_is_not_flagged_seasonal():
    rare = [30.0 if i % 3 == 0 else 0.0 for i in range(33)]  # продажа раз в квартал
    found = line(make_dataset({"A": rare}, stock_now=0, tx_lines=1))
    assert found is not None
    assert "intermittent" in found.flags
    assert "seasonal" not in found.flags
    assert "сезонный" not in found.explanation, found.explanation


def test_overstock_is_counted_even_when_nothing_is_ordered():
    result = engine.run(make_dataset({"A": noisy(100)}, stock_now=10_000), RunParams())
    assert not result.lines
    assert result.kpi.overstock_lines == 1


def test_warns_when_urgency_rests_on_estimated_stock():
    ds = make_dataset({"A": noisy(100)}, stock_now=0)
    ds.stock_now["source"] = "estimate_lower_bound"
    result = engine.run(ds, RunParams())
    assert result.lines[0].urgency == "critical"
    assert any("срочных" in w and "оценочн" in w for w in result.warnings), result.warnings


def test_old_oneoff_outside_base_window_is_not_the_lead_reason():
    ds = make_dataset({"A": noisy(100)})
    add_oneoff(ds, "A", date(2025, 2, 10), 2000)  # вне 12 закрытых месяцев базы (2025-09…2026-08)
    found = line(ds)
    assert not found.explanation.startswith("Разовый"), found.explanation


def test_fallback_reason_names_forecast_drivers():
    from app.engine.explain import _lead_reason

    reason = _lead_reason(month=pd.Period("2026-10", "M"), seasonal=False, season_val=1.21, trend_up=False,
                          trend_down=False, trend_val=1.046, stockout_restored=False, oneoff_excluded=False,
                          in_transit=0)
    assert "×1.21" in reason and "выше" in reason, reason
