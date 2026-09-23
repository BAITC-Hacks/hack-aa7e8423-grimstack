"""Обоснование заказа: components контракта (Component) и explanation-строка на русском."""

from app.contracts import Component

MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
              "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _waterfall(*, horizon, ss, floor, stock, transit, qty, moq, ss_label, approx_stock) -> list[Component]:
    """qty-компоненты: части округлены до 0.1 для показа, moq_rounding — точный остаток без повторного
    округления, поэтому их сумма строго равна qty при любой кратности, в том числе дробной."""
    parts = [
        Component(key="horizon_demand", label="Спрос на срок поставки и период заказа",
                   value=round(horizon, 1), kind="qty"),
        Component(key="safety_stock", label=ss_label, value=round(ss, 1), kind="qty"),
    ]
    if round(floor, 1) > 0:
        parts.append(Component(key="min_order_floor", label="Минимум для редкого спроса",
                                value=round(floor, 1), kind="qty", note="не меньше типичной строки продажи"))
    stock_note = "нижняя оценка: остаток на 01.09 минус продажи 1–21.09" if approx_stock else None
    parts += [
        Component(key="stock", label="Текущий остаток", value=round(-stock, 1), kind="qty", note=stock_note),
        Component(key="in_transit", label="В пути", value=round(-transit, 1), kind="qty"),
    ]
    parts.append(Component(key="moq_rounding", label=f"Округление до кратности {moq:g}",
                            value=qty - sum(c.value for c in parts), kind="qty"))
    return parts


def components_analyze(*, base, oneoff_excess, oneoff_note, restored, restored_note,
                        season_val, month, trend_val, growth_pct, category, sl,
                        horizon, ss, floor, stock, transit, moq, qty, approx_stock) -> list[Component]:
    comps = [Component(key="base", label="База: среднее 12 мес без сезонности",
                        value=round(base, 1), kind="info")]
    if oneoff_excess > 0:
        comps.append(Component(key="oneoff_excluded", label="Исключены разовые строки",
                                value=round(oneoff_excess, 1), kind="info", note=oneoff_note))
    if restored > 0:
        comps.append(Component(key="stockout_restored", label="Восстановлен упущенный спрос",
                                value=round(restored, 1), kind="info", note=restored_note))
    comps += [
        Component(key="seasonality", label=f"Сезонность {MONTHS_RU[month.month - 1]}",
                   value=round(season_val, 2), kind="factor"),
        Component(key="trend", label="Тренд год к году", value=round(trend_val, 2), kind="factor"),
        Component(key="growth", label="Прирост (параметр)", value=round(1 + growth_pct / 100, 2), kind="factor"),
    ]
    return comps + _waterfall(horizon=horizon, ss=ss, floor=floor, stock=stock, transit=transit, qty=qty,
                              moq=moq, ss_label=f"Страховой запас ({category or '—'}, {sl:.0%})",
                              approx_stock=approx_stock)


def components_baseline(*, base, horizon, ss, floor, stock, transit, moq, qty, sl, approx_stock) -> list[Component]:
    comps = [Component(key="base", label="Excel-метод: среднее 12 мес без очистки",
                        value=round(base, 1), kind="info")]
    return comps + _waterfall(horizon=horizon, ss=ss, floor=floor, stock=stock, transit=transit, qty=qty,
                              moq=moq, ss_label=f"Страховой запас ({sl:.0%})", approx_stock=approx_stock)


def _lead_reason(*, month, seasonal, season_val, trend_up, trend_down, trend_val,
                  stockout_restored, oneoff_excluded, in_transit) -> str:
    """Главная причина заказа: сезон → тренд → stockout → разовый заказ → товар в пути.

    Сезон лидирует только при заметном факторе (≥1.25 пик / ≤0.80 спад) —
    иначе флаг seasonal есть, но фактор ближайшего месяца слабый и причину подавать не стоит."""
    if seasonal and season_val >= 1.25:
        return f"{MONTHS_NOM[month.month - 1]} — сезонный пик (×{season_val:.2f})"
    if seasonal and season_val <= 0.80:
        return f"{MONTHS_NOM[month.month - 1]} — сезонный спад (×{season_val:.2f})"
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
