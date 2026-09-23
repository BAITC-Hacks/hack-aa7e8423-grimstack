from app.ingest import apply_product_groups
from tests.engine.factory import make_dataset, noisy


def test_product_groups_are_mapped_by_supplier_and_sku(tmp_path):
    ds = make_dataset({"A": noisy(10), "B": noisy(10)})
    csv = tmp_path / "sku_categories.csv"
    csv.write_text("supplier,sku,group,prob\nIEK,A,Кабель и провод,0.91\nSE,B,Розетки,0.80\n", encoding="utf-8")
    apply_product_groups(ds, csv)
    assert ds.skus.loc[("IEK", "A"), "product_group"] == "Кабель и провод"
    assert ds.skus.loc[("IEK", "B"), "product_group"] is None


def test_product_groups_without_file_leave_data_untouched(tmp_path):
    ds = make_dataset({"A": noisy(10)})
    apply_product_groups(ds, tmp_path / "missing.csv")
    assert ds.skus.loc[("IEK", "A"), "product_group"] is None
