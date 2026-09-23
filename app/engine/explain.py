"""Обоснование заказа: components контракта (Component) и explanation-строка на русском."""

from app.contracts import Component

MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _qty_parts(horizon: float, ss: float, stock: float, transit: float, qty: float) -> dict:
    """qty-компоненты округляются до 0.1; moq_rounding — остаток, водопад сходится точно."""
    parts = {"horizon_demand": round(horizon, 1), "safety_stock": round(ss, 1),
              "stock": round(-stock, 1), "in_transit": round(-transit, 1)}
    parts["moq_rounding"] = round(qty - sum(parts.values()), 1)
    return parts


def components_analyze(*, base, oneoff_excess, oneoff_note, restored, restored_note,
                        season_val, month, trend_val, growth_pct, category, sl,
                        horizon, ss, stock, transit, moq, qty, approx_stock) -> list[Component]:
    comps = [Component(key="base", label="База: среднее 12 мес без сезонности",
                        value=round(base, 1), kind="info")]
    if oneoff_excess > 0:
        comps.append(Component(key="oneoff_excluded", label="Исключены разовые строки",
                                value=round(oneoff_excess, 1), kind="info", note=oneoff_note))
    if restored > 0:
        comps.append(Component(key="stockout_restored", label="Восстановлен упущенный спрос",
                                value=round(restored, 1), kind="info", note=restored_note))
    comps += [
        Component(key="seasonality", label=f"Сезонность: {MONTHS_RU[month.month - 1]}",
                   value=round(season_val, 2), kind="factor"),
        Component(key="trend", label="Тренд год к году", value=round(trend_val, 2), kind="factor"),
        Component(key="growth", label="Прирост (параметр)", value=round(1 + growth_pct / 100, 2), kind="factor"),
    ]
    parts = _qty_parts(horizon, ss, stock, transit, qty)
    stock_note = "нижняя оценка: остаток на 01.09 минус продажи 1–21.09" if approx_stock else None
    comps += [
        Component(key="horizon_demand", label="Спрос на срок поставки и период заказа",
                   value=parts["horizon_demand"], kind="qty"),
        Component(key="safety_stock", label=f"Страховой запас ({category or '—'}, {sl:.0%})",
                   value=parts["safety_stock"], kind="qty"),
        Component(key="stock", label="Текущий остаток", value=parts["stock"], kind="qty", note=stock_note),
        Component(key="in_transit", label="В пути", value=parts["in_transit"], kind="qty"),
        Component(key="moq_rounding", label=f"Округление до кратности {moq:g}",
                   value=parts["moq_rounding"], kind="qty"),
    ]
    return comps


def components_baseline(*, base, horizon, ss, stock, transit, moq, qty, sl, approx_stock) -> list[Component]:
    comps = [Component(key="base", label="Excel-метод: среднее 12 мес без очистки",
                        value=round(base, 1), kind="info")]
    parts = _qty_parts(horizon, ss, stock, transit, qty)
    stock_note = "нижняя оценка: остаток на 01.09 минус продажи 1–21.09" if approx_stock else None
    comps += [
        Component(key="horizon_demand", label="Спрос на срок поставки и период заказа",
                   value=parts["horizon_demand"], kind="qty"),
        Component(key="safety_stock", label=f"Страховой запас ({sl:.0%})",
                   value=parts["safety_stock"], kind="qty"),
        Component(key="stock", label="Текущий остаток", value=parts["stock"], kind="qty", note=stock_note),
        Component(key="in_transit", label="В пути", value=parts["in_transit"], kind="qty"),
        Component(key="moq_rounding", label=f"Округление до кратности {moq:g}",
                   value=parts["moq_rounding"], kind="qty"),
    ]
    return comps


def _lead_reason(*, seasonal, season_val, trend_up, trend_down, trend_val,
                  stockout_restored, oneoff_excluded, in_transit) -> str:
    """Главная причина заказа: сезон → тренд → stockout → разовый заказ → товар в пути."""
    if seasonal and season_val >= 1.15:
        return f"Месяц — сезонный пик (×{season_val:.2f})"
    if seasonal and season_val <= 0.85:
        return f"Месяц — сезонный спад (×{season_val:.2f})"
    if trend_up:
        return f"Устойчивый рост спроса (×{trend_val:.2f} год к году)"
    if trend_down:
        return f"Спрос снижается (×{trend_val:.2f} год к году)"
    if stockout_restored:
        return "Восстановлен упущенный спрос (была нехватка товара)"
    if oneoff_excluded:
        return "Разовый крупный заказ исключён из базового спроса"
    if in_transit > 0:
        return "Товар уже в пути"
    return "Регулярный спрос"


def _fmt(x: float) -> str:
    """Целое число с пробелом-разделителем тысяч: 8944 → '8 944'."""
    return f"{x:,.0f}".replace(",", " ")


def explanation_analyze(*, unit, forecast_monthly, qty, moq, **reason_kwargs) -> str:
    lead = _lead_reason(**reason_kwargs)
    return (f"{lead}: прогноз {_fmt(forecast_monthly)} {unit}/мес"
            f" → {_fmt(qty)} {unit}, кратно {moq:g}")


def explanation_baseline(*, unit, avg_monthly, qty, moq, lr_days) -> str:
    return (f"Excel-метод: среднее {_fmt(avg_monthly)} {unit}/мес × {lr_days:.0f}/30"
            f" → {_fmt(qty)} {unit}, кратно {moq:g}")
