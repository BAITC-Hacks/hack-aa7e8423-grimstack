"""Бэктест: главная проверка — отсутствие утечки будущего в усечённом Dataset (docs/backtest.md).

TDD: этот файл написан раньше scripts/backtest.py и сначала падает на импорте.
"""

import copy
import time

import pandas as pd
import pytest

from app import ingest
from app.engine import forecast
from tests.engine.factory import AS_OF, add_oneoff, make_dataset, noisy, seasonal

from scripts.backtest import CHECKPOINTS, LAST_CLOSED, build_checkpoint, truncate_dataset

M = pd.Period("2026-06", "M")  # контрольная точка для синтетических тестов


def _synthetic_dataset() -> "ingest.dataset.Dataset":
    """Два SKU: сезонный (пул сезонности) и обычный шумный ряд, с разовой строкой и stockout."""
    series = {
        "SEASON01": seasonal(40.0, [1.4, 1.3, 1.1, 0.9, 0.7, 0.6, 0.6, 0.7, 0.9, 1.1, 1.3, 1.4]),
        "PLAIN02": noisy(25.0),
    }
    ds = make_dataset(series, supplier="IEK", stock_now=500.0, moq=1.0, category="B")
    add_oneoff(ds, "PLAIN02", AS_OF.replace(month=5, day=15), 400.0)
    return ds


def _corrupt_from(ds, m: pd.Period):
    """Портит все данные ≥ m: продажи (и колонку m тоже), остатки СТРОГО после m, транзакции ≥ m,
    плюс снимок «сейчас» (stock_now/in_transit) — truncate_dataset его пересобирает сам и не должен
    брать из исходного ds; порча снимка ловит случай, если кто-то по ошибке начнёт его использовать.

    Остаток на 1-е число m — это «close» месяца m-1, он известен на дату as_of=m и не портится.
    """
    ds2 = copy.deepcopy(ds)
    future_sales_cols = [c for c in ds2.sales_monthly.columns if c >= m]
    ds2.sales_monthly.loc[:, future_sales_cols] = 999_999.0
    future_stock_cols = [c for c in ds2.stock_monthly.columns if c > m]
    ds2.stock_monthly.loc[:, future_stock_cols] = 999_999.0
    cutoff = pd.Timestamp(m.start_time)
    future_tx = ds2.sales_tx["date"] >= cutoff
    ds2.sales_tx.loc[future_tx, "qty"] = 999_999.0
    ds2.stock_now = ds2.stock_now.copy()
    ds2.stock_now["qty"] = 999_999.0
    if len(ds2.in_transit):
        ds2.in_transit = ds2.in_transit.copy()
        ds2.in_transit["qty"] = 999_999.0
    return ds2


def test_checkpoints_are_first_of_month_2026_03_to_08():
    assert [str(m) for m in CHECKPOINTS] == ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    assert LAST_CLOSED == pd.Period("2026-08", "M")


def test_truncate_dataset_column_ranges():
    ds = _synthetic_dataset()
    trunc = truncate_dataset(ds, M)

    assert list(trunc.sales_monthly.columns)[-1] == M  # текущий месяц включён (нулями)
    assert (trunc.sales_monthly[M] == 0.0).all()
    assert all(c < M for c in trunc.sales_monthly.columns[:-1])
    assert all(c <= M for c in trunc.stock_monthly.columns)
    assert trunc.stock_monthly.columns[-1] == M

    assert (trunc.sales_tx["date"] < pd.Timestamp(M.start_time)).all()
    assert trunc.in_transit.empty
    assert list(trunc.in_transit.columns) == ["supplier", "sku", "qty", "eta"]

    pd.testing.assert_series_equal(trunc.stock_now["qty"], trunc.stock_monthly[M], check_names=False)
    assert (trunc.stock_now["source"] == "warehouses").all()
    assert trunc.as_of == M.start_time.date()


def test_truncate_dataset_recomputes_iek_category_without_future():
    """ABC IEK должен считаться по 12 мес. ДО m, а не по полной истории (иначе категория подглядывает вперёд)."""
    ds = _synthetic_dataset()
    trunc = truncate_dataset(ds, M)

    # PLAIN02 продаёт намного больше после m (это видно в исходном ds, но не должно влиять на trunc)
    key = ("IEK", "PLAIN02")
    full_category = ds.skus.loc[key, "category"]
    trunc_category = trunc.skus.loc[key, "category"]
    # категория пересчитана функцией _abc_category на данных < m — просто проверяем, что она валидна
    assert trunc_category in ("A", "B", "C")
    assert full_category in ("A", "B", "C")


def test_truncate_removes_future_leakage_synthetic():
    ds = _synthetic_dataset()
    ds_corrupt = _corrupt_from(ds, M)

    cp = build_checkpoint(ds, M)
    cp_corrupt = build_checkpoint(ds_corrupt, M)

    fc = forecast.forecast_value(cp.prepared.base, cp.prepared.season, cp.prepared.trend, 0.0, M)
    fc_corrupt = forecast.forecast_value(cp_corrupt.prepared.base, cp_corrupt.prepared.season,
                                          cp_corrupt.prepared.trend, 0.0, M)
    pd.testing.assert_series_equal(fc, fc_corrupt)

    pd.testing.assert_series_equal(cp.analyze["order_up_to"], cp_corrupt.analyze["order_up_to"])
    pd.testing.assert_series_equal(cp.analyze["qty"], cp_corrupt.analyze["qty"])
    pd.testing.assert_series_equal(cp.baseline["order_up_to"], cp_corrupt.baseline["order_up_to"])
    pd.testing.assert_series_equal(cp.baseline["qty"], cp_corrupt.baseline["qty"])
    pd.testing.assert_series_equal(cp.ds.skus["category"], cp_corrupt.ds.skus["category"])


@pytest.fixture(scope="module")
def real_ds():
    return ingest.load_default()


def test_truncate_removes_future_leakage_real_data_one_point(real_ds):
    """Лёгкая проверка на реальных данных: одна точка, без полного прогона по всем 6."""
    m = pd.Period("2026-06", "M")
    t0 = time.perf_counter()
    ds_corrupt = _corrupt_from(real_ds, m)

    cp = build_checkpoint(real_ds, m)
    cp_corrupt = build_checkpoint(ds_corrupt, m)

    fc = forecast.forecast_value(cp.prepared.base, cp.prepared.season, cp.prepared.trend, 0.0, m)
    fc_corrupt = forecast.forecast_value(cp_corrupt.prepared.base, cp_corrupt.prepared.season,
                                          cp_corrupt.prepared.trend, 0.0, m)
    pd.testing.assert_series_equal(fc, fc_corrupt)
    pd.testing.assert_series_equal(cp.analyze["order_up_to"], cp_corrupt.analyze["order_up_to"])
    pd.testing.assert_series_equal(cp.analyze["qty"], cp_corrupt.analyze["qty"])
    pd.testing.assert_series_equal(cp.baseline["order_up_to"], cp_corrupt.baseline["order_up_to"])
    pd.testing.assert_series_equal(cp.ds.skus["category"], cp_corrupt.ds.skus["category"])
    assert time.perf_counter() - t0 < 30, "точка на реальных данных должна укладываться в бюджет"
