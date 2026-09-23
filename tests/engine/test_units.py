"""Юнит-тесты очистки от разовых строк (cleaning) и восстановления stockout (stockout)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.engine.cleaning import detect_oneoffs, monthly_excess, pallet_mask
from app.engine.stockout import restore
from tests.engine.factory import add_oneoff, make_dataset, noisy


def _empty_tx() -> pd.DataFrame:
    return pd.DataFrame({"supplier": pd.Series(dtype=object), "sku": pd.Series(dtype=object),
                          "date": pd.Series(dtype="datetime64[ns]"), "doc": pd.Series(dtype=object),
                          "qty": pd.Series(dtype=float)})


# --- cleaning: разовые строки ---

def test_large_row_is_flagged_and_capped_to_median():
    ds = make_dataset({"A": noisy(20)}, tx_lines=1)
    add_oneoff(ds, "A", date(2026, 6, 10), 2000)

    events = detect_oneoffs(ds.sales_tx, ds.skus, ds.sales_monthly, ds.as_of)

    ev = events[events["qty"] == 2000]
    assert len(ev) == 1
    ev = ev.iloc[0]
    assert ev["capped_to"] == pytest.approx(20, rel=0.2)
    assert ev["excess"] == pytest.approx(2000 - ev["capped_to"])
    assert ev["excess"] == pytest.approx(1980, rel=0.2)


def test_regular_large_rows_across_many_months_are_not_flagged():
    ds = make_dataset({"A": noisy(20)}, tx_lines=1)
    for i in range(6):  # 6 разных месяцев подряд — это регулярный опт, а не разовые заказы
        add_oneoff(ds, "A", date(2025, 3 + i, 10), 200)

    events = detect_oneoffs(ds.sales_tx, ds.skus, ds.sales_monthly, ds.as_of)

    assert events[events["qty"] == 200].empty


def test_pallet_rows_are_excluded_and_caught_by_pallet_mask():
    ds = make_dataset({"A": noisy(100)}, supplier="SE", moq=3780, tx_lines=1)
    add_oneoff(ds, "A", date(2025, 5, 5), 7560)  # 2 × MOQ — паллетная поставка

    mask = pallet_mask(ds.sales_tx, ds.skus)
    assert (ds.sales_tx.loc[mask, "qty"] == 7560).any()

    events = detect_oneoffs(ds.sales_tx, ds.skus, ds.sales_monthly, ds.as_of)
    assert events[events["qty"] == 7560].empty


def test_monthly_excess_places_amount_in_right_month():
    ds = make_dataset({"A": noisy(20)}, tx_lines=1)
    add_oneoff(ds, "A", date(2026, 6, 10), 2000)

    events = detect_oneoffs(ds.sales_tx, ds.skus, ds.sales_monthly, ds.as_of)
    excess = monthly_excess(events, ds.sales_monthly)

    assert excess.shape == ds.sales_monthly.shape
    june = pd.Period("2026-06", "M")
    assert excess.loc[("IEK", "A"), june] == pytest.approx(1980, rel=0.2)
    other = excess.loc[("IEK", "A")].drop(june)
    assert (other == 0).all()


# --- stockout: типы месяцев и восстановление ---

def test_full_month_added_about_base_and_normal_month_zero():
    idx = pd.MultiIndex.from_tuples([("IEK", "A")], names=["supplier", "sku"])
    months = pd.period_range("2026-01", "2026-07", freq="M")
    stock_cols = pd.period_range("2026-01", "2026-08", freq="M")
    cleaned = pd.DataFrame([[100.0] * 6 + [0.0]], index=idx, columns=months)
    # в наличии (500) до июля, в июле остаток исчерпан (open=0, close=0) — full
    stock_monthly = pd.DataFrame([[500.0] * 6 + [0.0, 0.0]], index=idx, columns=stock_cols)

    added, types = restore(cleaned, stock_monthly, _empty_tx())

    jul = pd.Period("2026-07", "M")
    assert types.loc[("IEK", "A"), jul] == "full"
    assert added.loc[("IEK", "A"), jul] == pytest.approx(100.0, rel=0.2)

    may = pd.Period("2026-05", "M")
    assert types.loc[("IEK", "A"), may] is None
    assert added.loc[("IEK", "A"), may] == 0


def test_start_2024_uses_default_half_base_share():
    idx = pd.MultiIndex.from_tuples([("IEK", "A")], names=["supplier", "sku"])
    months = pd.period_range("2024-01", "2024-07", freq="M")
    stock_cols = pd.period_range("2024-01", "2024-08", freq="M")
    cleaned = pd.DataFrame([[100.0] * 6 + [0.0]], index=idx, columns=months)
    # июль: остаток на начало 0 (start), к августу приход (close > 0)
    stock_monthly = pd.DataFrame([[500.0] * 6 + [0.0, 100.0]], index=idx, columns=stock_cols)

    added, types = restore(cleaned, stock_monthly, _empty_tx())

    jul = pd.Period("2024-07", "M")
    assert types.loc[("IEK", "A"), jul] == "start"
    assert added.loc[("IEK", "A"), jul] == pytest.approx(50.0, rel=0.2)  # ≈ 0.5 · база(100)


def test_end_2025_share_computed_from_transactions():
    idx = pd.MultiIndex.from_tuples([("IEK", "A")], names=["supplier", "sku"])
    months = pd.period_range("2025-01", "2025-07", freq="M")
    stock_cols = pd.period_range("2025-01", "2025-08", freq="M")
    cleaned = pd.DataFrame([[100.0] * 6 + [80.0]], index=idx, columns=months)
    # июль: остаток был (100), к августу — stockout (0) → end
    stock_monthly = pd.DataFrame([[500.0] * 6 + [100.0, 0.0]], index=idx, columns=stock_cols)
    tx = pd.DataFrame([
        {"supplier": "IEK", "sku": "A", "date": pd.Timestamp("2025-07-10"), "doc": "d1", "qty": 60.0},
        {"supplier": "IEK", "sku": "A", "date": pd.Timestamp("2025-07-20"), "doc": "d2", "qty": 50.0},
    ])  # накопленные продажи достигают open=100 на второй строке (60+50=110)

    added, types = restore(cleaned, stock_monthly, tx)

    jul = pd.Period("2025-07", "M")
    assert types.loc[("IEK", "A"), jul] == "end"
    total_wd = float(np.busday_count(date(2025, 7, 1), date(2025, 8, 1), weekmask="1111110"))
    used_wd = float(np.busday_count(date(2025, 7, 1), date(2025, 7, 21), weekmask="1111110"))
    expected = min(100 * (1 - used_wd / total_wd), 200.0)
    assert added.loc[("IEK", "A"), jul] == pytest.approx(expected)


def test_start_2025_share_from_first_sale_or_zero_without_sales():
    idx = pd.MultiIndex.from_tuples([("IEK", "A"), ("IEK", "B")], names=["supplier", "sku"])
    months = pd.period_range("2025-01", "2025-07", freq="M")
    stock_cols = pd.period_range("2025-01", "2025-08", freq="M")
    cleaned = pd.DataFrame([[100.0] * 6 + [0.0], [100.0] * 6 + [0.0]], index=idx, columns=months)
    # июль: остаток на начало 0 (start), к августу пришёл товар
    stock_monthly = pd.DataFrame([[500.0] * 6 + [0.0, 90.0], [500.0] * 6 + [0.0, 90.0]],
                                  index=idx, columns=stock_cols)
    tx = pd.DataFrame([{"supplier": "IEK", "sku": "A", "date": pd.Timestamp("2025-07-15"),
                        "doc": "d1", "qty": 30.0}])  # у B продаж в июле нет вовсе

    added, types = restore(cleaned, stock_monthly, tx)

    jul = pd.Period("2025-07", "M")
    assert types.loc[("IEK", "A"), jul] == "start"
    assert types.loc[("IEK", "B"), jul] == "start"
    total_wd = float(np.busday_count(date(2025, 7, 1), date(2025, 8, 1), weekmask="1111110"))
    used_wd = float(np.busday_count(date(2025, 7, 15), date(2025, 8, 1), weekmask="1111110"))
    assert added.loc[("IEK", "A"), jul] == pytest.approx(min(100 * (1 - used_wd / total_wd), 200.0))
    assert added.loc[("IEK", "B"), jul] == pytest.approx(100.0)  # нет продаж → доля 0 → добавка = база
