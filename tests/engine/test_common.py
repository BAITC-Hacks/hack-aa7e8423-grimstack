import pandas as pd

from app.ingest.common import doc_hash, month_period, norm_code


def test_month_period_parses_1c_headers():
    assert month_period("янв. 2024") == pd.Period("2024-01", "M")
    assert month_period("февр. 2025") == pd.Period("2025-02", "M")
    assert month_period("май 2026") == pd.Period("2026-05", "M")
    assert month_period("сент. 2026") == pd.Period("2026-09", "M")
    assert month_period("Январь 2024 г.") == pd.Period("2024-01", "M")
    assert month_period("Сентябрь 2026 г.") == pd.Period("2026-09", "M")


def test_month_period_rejects_non_months():
    for label in ("Итого", "Продажи 2024", "Ср мес 2024", None, 3.5, "нач. остаток"):
        assert month_period(label) is None


def test_norm_code_keeps_underscore_and_drops_junk():
    assert norm_code(" 250600007_ ") == "250600007_"
    assert norm_code("250600007") == "250600007"
    for junk in (None, "", "nan", float("nan"), "Итого"):
        assert norm_code(junk) is None


def test_doc_hash_is_stable_and_short():
    a = doc_hash("Расходная накладная 20000064179 от 09.06.2025")
    assert a == doc_hash("Расходная накладная 20000064179 от 09.06.2025")
    assert len(a) == 10 and "20000064179" not in a


def test_read_sales_tx_without_file_is_empty(tmp_path):
    from app.ingest.common import read_sales_tx

    tx = read_sales_tx(tmp_path / "sales_tx.xlsx", "IEK")
    assert tx.empty
    assert list(tx.columns) == ["supplier", "sku", "date", "doc", "qty"]
