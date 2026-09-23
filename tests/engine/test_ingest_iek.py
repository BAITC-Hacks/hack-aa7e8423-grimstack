"""Загрузчик IEK на реальных выгрузках (docs/data-profile.md §1, §2, §4, §5).

Датасет читается один раз на модуль (fixture module-scoped): чтение ~10 с.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.contracts import IngestError
from app.ingest import iek, load_uploaded

AS_OF = date(2026, 9, 22)
FOLDER = Path("data/raw/iek")


@pytest.fixture(scope="module")
def ds():
    return iek.load(FOLDER, AS_OF)


def sku_row(ds, sku):
    return ds.skus.xs(sku, level="sku").iloc[0]


def test_sales_monthly_universe_and_columns(ds):
    active = (ds.sales_monthly.sum(axis=1) != 0).sum()
    assert 2460 * 0.97 <= active <= 2460 * 1.03
    assert list(ds.sales_monthly.columns) == list(pd.period_range("2024-01", "2026-09", freq="M"))
    assert not ds.sales_monthly.isna().any().any()
    assert not ds.stock_monthly.isna().any().any()


def test_in_transit_total_and_eta(ds):
    total = ds.in_transit["qty"].sum()
    assert 109517 * 0.99 <= total <= 109517 * 1.01
    n_sku = ds.in_transit["sku"].nunique()
    assert 300 * 0.99 <= n_sku <= 300 * 1.01
    assert ds.in_transit["eta"].min() >= pd.Timestamp("2026-09-30")
    assert ds.in_transit["eta"].max() <= pd.Timestamp("2026-10-15")


def test_sku_010500006_article_and_moq(ds):
    row = sku_row(ds, "010500006_")
    assert row["article"] == "MVA20-1-016-C"
    assert row["moq"] == 12
    assert "ВА47-29" in row["name"]


def test_sales_tx_has_loop_row(ds):
    tx = ds.sales_tx
    match = tx[(tx["sku"] == "130200305_") & (tx["qty"] == 210000)]
    assert len(match) == 1


def test_stock_now_lower_bound(ds):
    row = sku_row(ds, "130300792_")
    assert row is not None
    now = ds.stock_now.xs("130300792_", level="sku").iloc[0]
    assert now["qty"] == max(0, 7230 - 8525)
    assert now["source"] == "estimate_lower_bound"


def _real_files() -> dict[str, bytes]:
    return {role: (FOLDER / f"{role}.xlsx").read_bytes()
            for role in ("monthly_sales", "monthly_stock", "sales_tx", "in_transit", "moq", "seasonality")}


def test_load_uploaded_real_files_returns_dataset():
    result = load_uploaded("IEK", _real_files())
    assert len(result.skus) > 0


def test_load_uploaded_bad_xlsx_raises_bad_format():
    files = _real_files()
    files["monthly_sales"] = b"PK" + b"garbage-not-a-real-xlsx" * 5
    with pytest.raises(IngestError) as exc:
        load_uploaded("IEK", files)
    assert exc.value.code == "bad_format"
