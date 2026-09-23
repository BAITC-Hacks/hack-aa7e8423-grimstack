"""AI-слой: LLM-сводка по заказу поставщику. Владелец — ядро (А)."""

from app.contracts import RunResult, SummaryResponse, Supplier


def summary(result: RunResult, supplier: Supplier) -> SummaryResponse | None:
    """None — нет ни ключа, ни кэша для этого входа (API → 503)."""
    return None
