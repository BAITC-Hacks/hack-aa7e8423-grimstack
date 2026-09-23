"""Загрузчик Systeme Electric на реальных выгрузках (docs/data-profile.md §1, §2, §4, §5)."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.ingest import se

AS_OF = date(2026, 9, 22)
FOLDER = Path("data/raw/se")


@pytest.fixture(scope="module")
def ds():
    return se.load(FOLDER, AS_OF)


def sku_row(ds, sku):
    return ds.skus.xs(sku, level="sku").iloc[0]


def test_sales_monthly_universe_and_columns(ds):
    # вселенная = monthly_sales ∪ monthly_stock ∪ TDSheet ∪ moq — она больше, чем сам monthly_sales,
    # но SKU с реальными продажами (пришли из monthly_sales.xlsx) — около 554, ±5%
    active = (ds.sales_monthly.sum(axis=1) != 0).sum()
    assert 554 * 0.95 <= active <= 554 * 1.05
    assert list(ds.sales_monthly.columns) == list(pd.period_range("2024-01", "2026-09", freq="M"))
    assert not ds.sales_monthly.isna().any().any()
    assert (ds.sales_monthly.index.get_level_values("supplier") == "SE").all()
    # skus/sales_monthly/stock_monthly обязаны жить на одной вселенной (общий индекс)
    assert ds.skus.index.equals(ds.sales_monthly.index)
    assert ds.stock_monthly.index.equals(ds.sales_monthly.index)


def test_in_transit_50160_units_for_7_skus(ds):
    assert len(ds.in_transit) == 7
    assert ds.in_transit["qty"].sum() == 50160
    assert (ds.in_transit["supplier"] == "SE").all()
    assert (ds.in_transit["eta"] == pd.Timestamp(2026, 9, 24)).all()


def test_030200193_from_warehouses():
    result = se.load(FOLDER, AS_OF)
    row = sku_row(result, "030200193_")
    assert row["moq"] == 3780
    assert row["unit_cost"] == pytest.approx(133.71)
    assert row["category"] == "Кат. 1"
    stock = result.stock_now.xs("030200193_", level="sku").iloc[0]
    assert stock["qty"] == 40798  # 39531 + 1 + 0 + 0 + 1266
    assert stock["source"] == "warehouses"


def test_300200428_stock_now():
    result = se.load(FOLDER, AS_OF)
    stock = result.stock_now.xs("300200428_", level="sku").iloc[0]
    assert stock["qty"] == 1174


def test_030200201_sales_2025_sum_over_200000():
    result = se.load(FOLDER, AS_OF)
    months_2025 = [p for p in result.sales_monthly.columns if p.year == 2025]
    total = result.sales_monthly.xs("030200201_", level="sku")[months_2025].sum().sum()
    assert total > 200000
