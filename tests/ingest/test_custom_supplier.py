"""Uploaded template data must be isolated under its new supplier code."""

import pytest

from app.ingest.dataset import rename_supplier
from tests.engine.factory import make_dataset, noisy


@pytest.mark.parametrize("template", ["IEK", "SE"])
def test_rename_supplier_updates_all_indexed_and_row_tables(template):
    dataset = make_dataset(
        {"A": noisy(10)}, supplier=template, stock_now=0, in_transit=3,
    )
    renamed = rename_supplier(
        dataset, "CUSTOM_1234abcd", name="Новый поставщик", template=template,
        lead_time_days=24, review_period_days=7,
    )

    assert renamed is dataset
    for attribute in ("skus", "sales_monthly", "stock_monthly", "stock_now"):
        index = getattr(renamed, attribute).index
        assert index.names == ["supplier", "sku"]
        assert index.tolist() == [("CUSTOM_1234abcd", "A")]
    for attribute in ("sales_tx", "in_transit"):
        assert set(getattr(renamed, attribute)["supplier"]) == {"CUSTOM_1234abcd"}
    assert renamed.metadata["custom_suppliers"]["CUSTOM_1234abcd"] == {
        "name": "Новый поставщик", "template": template,
        "lead_time_days": 24, "review_period_days": 7,
    }
