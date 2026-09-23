from datetime import date

import pandas as pd

from app.engine import pipeline
from scripts.backtest import snapshot
from tests.engine.factory import make_dataset, noisy


def test_snapshot_does_not_see_future_sales_or_transactions():
    source = make_dataset({"sku": noisy(100)})
    cutoff = date(2026, 4, 1)
    first = snapshot(source, cutoff)
    source.sales_monthly.loc[("IEK", "sku"), pd.Period("2026-04", "M")] = 100_000
    source.sales_tx.loc[source.sales_tx["date"] >= pd.Timestamp(cutoff), "qty"] = 100_000
    second = snapshot(source, cutoff)

    assert first.sales_monthly.equals(second.sales_monthly)
    assert first.sales_tx.equals(second.sales_tx)
    assert first.stock_monthly.columns[-1] == pd.Period("2026-04", "M")
    assert pipeline._prepare(first).base.equals(pipeline._prepare(second).base)
