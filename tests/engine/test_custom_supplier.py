"""Новый поставщик рассчитывается по шаблону выгрузки и своим параметрам."""

from datetime import date

from app import engine
from app.ai.llm import build_facts
from app.contracts import RunParams
from app.engine.cleaning import detect_oneoffs, pallet_mask
from app.ingest.dataset import concat
from tests.engine.factory import add_oneoff, make_dataset, noisy


CUSTOM = "CUSTOM_a1b2c3d4"


def _custom_dataset(template: str = "IEK"):
    ds = make_dataset({"A": noisy(100)}, supplier=CUSTOM, stock_now=0)
    ds.metadata = {"custom_suppliers": {CUSTOM: {
        "name": "Новый поставщик", "template": template,
        "lead_time_days": 38 if template == "IEK" else 45,
        "review_period_days": 9 if template == "IEK" else 30,
    }}}
    return ds


def test_custom_supplier_metadata_filter_defaults_and_history():
    ds = _custom_dataset()
    info = next(s for s in engine.meta(ds).suppliers if s.supplier == CUSTOM)
    assert (info.supplier_name, info.lead_time_days, info.review_period_days) == ("Новый поставщик", 38, 9)

    params = RunParams(supplier=CUSTOM)
    result = engine.run(ds, params)
    assert len(result.suppliers) == 1
    assert result.suppliers[0].supplier_name == "Новый поставщик"
    assert result.lines and {line.supplier for line in result.lines} == {CUSTOM}
    explicit = engine.run(ds, RunParams(supplier=CUSTOM, lead_time_days=38, review_period_days=9))
    assert [line.recommended_qty for line in result.lines] == [line.recommended_qty for line in explicit.lines]
    assert engine.history(ds, CUSTOM, "A", params).supplier == CUSTOM


def test_custom_supplier_remains_separate_from_builtin_with_same_sku():
    custom = _custom_dataset()
    builtin = make_dataset({"A": noisy(50)}, supplier="IEK", stock_now=0)
    ds = concat([builtin, custom])
    all_lines = engine.run(ds, RunParams()).lines
    assert {line.line_id for line in all_lines} == {"IEK:A", f"{CUSTOM}:A"}
    assert len(engine.run(ds, RunParams(supplier=CUSTOM)).lines) == 1
    assert next(s for s in engine.meta(ds).suppliers if s.supplier == CUSTOM).supplier_name == "Новый поставщик"


def test_se_template_applies_pallet_rule_to_custom_code():
    ds = make_dataset({"A": noisy(100)}, supplier=CUSTOM, moq=3780, tx_lines=1)
    add_oneoff(ds, "A", date(2025, 5, 5), 7560)
    mask = pallet_mask(ds.sales_tx, ds.skus, se_suppliers={"SE", CUSTOM})
    assert (ds.sales_tx.loc[mask, "qty"] == 7560).any()
    events = detect_oneoffs(ds.sales_tx, ds.skus, ds.sales_monthly, ds.as_of,
                            se_suppliers={"SE", CUSTOM})
    assert events[events["qty"] == 7560].empty

    # Проверяем, что именно pipeline получает шаблон из metadata, а не смотрит на код SE.
    ds.metadata = {"custom_suppliers": {CUSTOM: {
        "name": "Новый поставщик", "template": "SE",
        "lead_time_days": 40, "review_period_days": 30,
    }}}
    line = engine.run(ds, RunParams(supplier=CUSTOM)).lines[0]
    assert "oneoff_excluded" not in line.flags


def test_ai_warning_is_scoped_to_dynamic_supplier():
    custom = _custom_dataset()
    custom.stock_now["source"] = "estimate_lower_bound"
    other = make_dataset({"B": noisy(100)}, supplier="IEK", stock_now=0)
    result = engine.run(concat([custom, other]), RunParams())
    custom_warning = next(w for w in result.warnings if w.startswith(f"{CUSTOM}:"))
    assert custom_warning not in build_facts(result, "IEK")["warnings"]
    assert custom_warning in build_facts(result, CUSTOM)["warnings"]
