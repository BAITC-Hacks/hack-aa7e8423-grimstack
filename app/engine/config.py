"""Параметры расчёта по умолчанию (docs/design.md §3–4). Менеджер переопределяет их через RunParams."""

SUPPLIERS = {
    "IEK": {"name": "IEK (ИЭК)", "lead_time_days": 24, "review_period_days": 7},
    "SE": {"name": "Systeme Electric", "lead_time_days": 40, "review_period_days": 30},
}


def suppliers_for_dataset(ds) -> dict[str, dict]:
    """Встроенные настройки и поставщики, добавленные к конкретной выгрузке."""
    metadata = getattr(ds, "metadata", None) or {}
    custom = metadata.get("custom_suppliers", {})
    return {**SUPPLIERS, **{code: cfg for code, cfg in custom.items() if code not in SUPPLIERS}}


def se_template_suppliers(ds) -> set[str]:
    """Особые правила SE применяются по шаблону файла, а не по коду поставщика."""
    return {"SE"} | {
        code for code, cfg in suppliers_for_dataset(ds).items()
        if cfg.get("template") == "SE"
    }


SERVICE_LEVEL = {"A": 0.97, "B": 0.95, "C": 0.90,
                 "Кат. 1": 0.97, "Кат. 2": 0.95, "Кат. 3": 0.90, "Кат. 5": 0.95}
DEFAULT_SERVICE_LEVEL = 0.95
DO_NOT_ORDER_CATEGORIES = {"Кат. 7"}
