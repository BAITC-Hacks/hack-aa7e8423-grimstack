"""Прогрев кэша LLM-сводок, чтобы у жюри без ключа кнопка «Сводка» отдавала сохранённый ответ.

Запуск из корня репозитория с живым ключом, затем закоммитить samples/cache/*.json:
    OPENAI_API_KEY=... [OPENAI_BASE_URL=... MODEL=...] python scripts/warm_llm_cache.py

Греет расчёты с параметрами по умолчанию, как их отправляет экран: все поставщики и каждый по
отдельности, наш метод и Excel-метод. Выжимки у этих прогонов разные, поэтому нужен каждый.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import engine, ingest  # noqa: E402
from app.ai import summary  # noqa: E402
from app.contracts import RunParams  # noqa: E402


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("Нужен OPENAI_API_KEY (и при необходимости OPENAI_BASE_URL, MODEL)")
    data = ingest.load_default()
    failed = 0
    for method in ("analyze", "baseline"):
        for scope in (None, "IEK", "SE"):
            result = engine.run(data, RunParams(supplier=scope, method=method))
            for head in result.suppliers:
                answer = summary(result, head.supplier)
                live = answer is not None and not answer.cached
                failed += not live
                print(f"{method:8} {scope or 'все':4} {head.supplier:3} → {'записано' if live else 'LLM не ответила'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
