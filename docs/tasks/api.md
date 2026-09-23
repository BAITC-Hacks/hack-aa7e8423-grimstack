# Задание Б: API, надёжность, экспорт

Промпт для Codex: вставить целиком.

---

Ты работаешь в репозитории команды GrimStack (хакатон HackAlem AI, кейс «Автоматический
расчёт заказов поставщикам»). Прочитай `docs/design.md`, разделы 2 и 6, и файл
`app/contracts.py`. Контракт заморожен, **не меняй его**. Если без изменения не обойтись,
остановись и спроси.

## Твои файлы (правишь только их)

`app/__main__.py`, `app/main.py`, `app/api/**`, `app/store.py`, `tests/api/**`,
`requirements.txt`, `.env.example`.

Файлы `app/contracts.py`, `app/ingest/**`, `app/engine/**`, `app/ai/**`, `frontend/**`,
`docs/**` принадлежат другим участникам. Их ты только читаешь.

## Ядро, которое вызываешь

Вызывай функции из `app.ingest`, `app.engine` и `app.ai` строго по сигнатурам из
`docs/design.md` §6. Сейчас они возвращают моки `contracts/*.json`, позже там появится
настоящий расчёт. Твой код от этого меняться не должен.

## Что сделать

1. **`app/main.py`**
   - FastAPI-приложение, все роутеры под префиксом `/api`.
   - `GET /health` → `{"status": "ok"}`.
   - Обработчики ошибок, все отвечают `ErrorBody` (`{"detail", "code", "meta"}`):
     - `IngestError` → 422, в `meta` класть `file_role`;
     - `RequestValidationError` → 422, `detail` по-русски;
     - `HTTPException` → её код;
     - любое другое исключение → 500 без стектрейса наружу.
   - Статика: если существует `frontend/dist`, монтировать её на `/`. На любой путь,
     который не начинается с `/api`, отдавать `index.html` (SPA-fallback).
2. **`app/__main__.py`** — `python -m app` запускает uvicorn на `0.0.0.0:$PORT`
   (по умолчанию 8000).
3. **`app/store.py`**
   - При старте (lifespan) один раз загрузить `ingest.load_default()`.
   - Хранить в памяти: датасеты по id и прогоны `RunResult` по `run_id`.
   - Утверждения дописывать в `var/approvals.json` (папка в `.gitignore`).
4. **`app/api/`** — эндпоинты из таблицы в §6 `docs/design.md`:
   - `POST /api/runs`: выбрать датасет по `params.dataset_id` (неизвестный → 404), вызвать
     `engine.run`, сохранить результат.
   - `PATCH /api/runs/{run_id}/lines/{line_id}`: тело `LinePatch`.
     - Количество < 0 → 422.
     - Количество не кратно `line.moq` → 422 с текстом «Количество должно быть кратно N».
     - Поставщик уже утверждён → 409.
     - При успехе пересчитать `amount` строки, итоги `SupplierSummary` и `kpi.total_amount`.
   - `POST /api/runs/{run_id}/suppliers/{supplier}/approve` → `status="approved"`,
     `approved_at`, запись в `var/approvals.json`. Автоматически никуда не отправлять:
     так требует ТЗ.
   - `GET /api/runs/{run_id}/export.xlsx?supplier=IEK` — openpyxl.
     - Строки с `final_qty > 0`.
     - Колонки: Код 1С · Артикул поставщика · Наименование · Ед. · Количество · Цена ·
       Сумма · Поставщик · Срочность · Обоснование.
     - Имя файла `order_<supplier>_<YYYY-MM-DD>.xlsx`.
   - `GET /api/sku/{supplier}/{sku}/history` → `engine.history(...)`.
   - `GET /api/meta` → `engine.meta(...)`.
   - `POST /api/datasets/{supplier}` — multipart, поле формы = роль файла (роли в
     `app.ingest.ROLES`). Вызвать `ingest.load_uploaded` и вернуть `DatasetUploaded`.
     Лимит 30 МБ на файл, при превышении — 422.
   - `POST /api/runs/{run_id}/summary?supplier=IEK` → `ai.summary(...)`. Если вернулся
     `None` — 503 с `detail`: «LLM недоступна: нет ключа и кэша».
   - Неизвестный `run_id`, `line_id` или `supplier` → 404.
5. **`tests/api/`** — pytest + `fastapi.testclient`, на каждый пункт выше хотя бы один
   тест, включая негативные: 404, 409, 422 на отрицательное и некратное количество,
   пустой файл, не-xlsx, 503 на сводку.
6. **`.env.example`** — `OPENAI_API_KEY=`, `OPENAI_BASE_URL=`, `MODEL=`, `PORT=8000`.
   Ключей не коммитить.

Docker и деплой на Northflank делает Aliar. От тебя нужно одно: сервис запускается
командой `python -m app` и слушает `$PORT`.

## Готово, когда

- `pytest -q` зелёный.
- `python -m app` поднимает сервис, а `curl localhost:8000/health` отвечает `ok`.
- `POST /api/runs` отдаёт `RunResult`.
- Экспорт открывается в Excel.

## Правила репозитория

- Работаем в `main`, перед каждым коммитом `git pull --rebase`.
- Коммит каждый час под своим аккаунтом с осмысленным сообщением.
- Если нужна новая зависимость, добавь её в `requirements.txt` с версией: это твой файл.

## Контрольные точки

- 1:00 — каркас и эндпоинты на моках.
- 2:00 — сквозной путь с фронтом.
- 4:00 — API на реальном ядре, все тесты зелёные.
- 4:00–5:00 — проверка чистого клона по README.
