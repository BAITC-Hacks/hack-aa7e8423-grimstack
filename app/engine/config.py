"""Параметры расчёта по умолчанию (docs/design.md §3–4). Менеджер переопределяет их через RunParams."""

SUPPLIERS = {
    "IEK": {"name": "IEK (ИЭК)", "lead_time_days": 24, "review_period_days": 7},
    "SE": {"name": "Systeme Electric", "lead_time_days": 40, "review_period_days": 30},
}

SERVICE_LEVEL = {"A": 0.97, "B": 0.95, "C": 0.90,
                 "Кат. 1": 0.97, "Кат. 2": 0.95, "Кат. 3": 0.90, "Кат. 5": 0.95}
DEFAULT_SERVICE_LEVEL = 0.95
DO_NOT_ORDER_CATEGORIES = {"Кат. 7"}
