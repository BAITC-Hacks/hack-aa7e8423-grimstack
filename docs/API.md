<div align="center">

# HTTP API GrimStack

Контракт локального моносервиса для интерфейса планирования заказов.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141.1-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-2.13.5-E92063?style=for-the-badge&logo=pydantic&logoColor=white)

</div>

## Содержание

- [Общие правила](#общие-правила)
- [Маршруты](#маршруты)
- [Ошибки](#ошибки)
- [Ограничения](#ограничения)

## Общие правила

Сервер запускается командой `python -m app`. Все маршруты данных имеют префикс `/api`; `GET /health` возвращает `{"status":"ok"}`. Формы ответа заданы в `app/contracts.py`, а TypeScript-зеркало — в `frontend/src/api/types.ts`. Авторизации нет. Прогоны и загруженные наборы хранятся в памяти процесса, утверждения — в SQLite `var/app.db`.

## Маршруты

Таблица описывает входные данные, результат и изменения состояния основных маршрутов.

| Метод | Путь | Вход | Успешный ответ и побочный эффект |
| :-- | :-- | :-- | :-- |
| `GET` | `/api/meta` | Необязательный `dataset_id` в query | `Meta`: дата данных, поставщики и категории выбранного набора, настройки, доступность LLM |
| `POST` | `/api/runs` | JSON `RunParams`; `dataset_id` необязателен | `RunResult`; новый прогон сохраняется в памяти |
| `GET` | `/api/runs/{run_id}` | Идентификатор прогона | Сохранённый `RunResult` |
| `PATCH` | `/api/runs/{run_id}/lines/{line_id}` | JSON `LinePatch`: `final_qty` | `OrderLine`; обновляет сумму строки, поставщика и KPI |
| `POST` | `/api/runs/{run_id}/suppliers/{supplier}/approve` | Код поставщика из прогона | `SupplierSummary` со статусом `approved`; первое утверждение записывается на диск, повторное возвращает тот же статус |
| `GET` | `/api/runs/{run_id}/export.xlsx?supplier=SE` | Поставщик | XLSX с положительными `final_qty`; скачивание не утверждает заказ |
| `GET` | `/api/sku/{supplier}/{sku}/history` | Поставщик, код 1С и необязательный `run_id` | `SkuHistory` с сырым, очищенным и восстановленным рядом |
| `POST` | `/api/datasets/{supplier}` | `multipart/form-data`, поле на роль файла | `DatasetUploaded` с `dataset_id`; набор хранится в памяти |
| `POST` | `/api/datasets/new` | `multipart/form-data`: текстовые `name`, `template` (`IEK` или `SE`) и файлы по ролям | `DatasetUploaded`: `dataset_id`, безопасный код `supplier`, отображаемое `supplier_name`, `warnings`; набор хранится в памяти |
| `POST` | `/api/runs/{run_id}/summary?supplier=SE` | Поставщик | `SummaryResponse` из LLM или кэша |
| `POST` | `/api/runs/compare` | JSON `{"base": RunParams, "scenario": RunParams}` | `CompareResult`: оба прогона сохраняются, разница KPI и товары с наибольшим изменением заказа |
| `GET` | `/api/approvals` | Нет | История утверждённых заказов из SQLite, последние сверху |
| `GET` | `/api/backtest` | Нет | Отчёт бэктеста `data/backtest/report.json` без пересчёта |

### Загрузка данных

Для загрузки обязательны файлы `monthly_sales`, `monthly_stock`, `in_transit`, `moq`. Файлы `sales_tx` и `seasonality` необязательны. Без `sales_tx` ответ содержит предупреждение: анализ разовых заказов и дней наличия ограничен. Каждый файл — XLSX до 30 МБ.

Для **нового поставщика** в модальном окне укажите название и выберите, как устроены его выгрузки 1С: **как у IEK** или **как у Systeme Electric**. API принимает те же данные через `POST /api/datasets/new`: `name` (от 2 до 80 символов), `template=IEK` или `template=SE` и перечисленные выше файлы. Шаблон определяет, как читать файлы и какие сроки L/R взять по умолчанию; произвольная структура XLSX не поддерживается. Ответ содержит отдельный код поставщика вида `CUSTOM_...` в поле `supplier` и введённое название в `supplier_name`. Код нужно брать из ответа, а не составлять вручную.

После загрузки вызовите `GET /api/meta?dataset_id=<dataset_id>`, чтобы получить поставщика и категории загруженного набора. Для расчёта передайте **оба поля из ответа загрузки** в `POST /api/runs`, например `{"dataset_id":"<dataset_id>","supplier":"<supplier>"}`. Интерфейс выбирает их после загрузки автоматически. Вызовы без `dataset_id` продолжают использовать встроенный набор `data/raw/`; новый поставщик в нём не появляется. Загруженный набор и его идентификатор доступны только до перезапуска процесса.

### Экспорт заказа

Экспорт содержит колонки: Код 1С, Артикул поставщика, Наименование, Ед., Количество, Цена, Сумма, Поставщик, Срочность, Обоснование. Имя файла: `order_<supplier>_<YYYY-MM-DD>.xlsx`.

## Ошибки

Любая ошибка API возвращает единый JSON-объект:

```json
{"detail": "...", "code": "...", "meta": {}}
```

При ошибке конкретного файла `meta.file_role` содержит роль, если она известна.

| HTTP | `code` | Условие |
| :-- | :-- | :-- |
| 404 | `not_found` | Неизвестный прогон, строка, SKU, поставщик или набор |
| 404 | `backtest_not_found` | Нет файла `data/backtest/report.json` |
| 409 | `conflict` | Правка утверждённого заказа |
| 422 | `validation_error` | Некорректные поля JSON |
| 422 | `invalid_input` | Некратное количество, слишком большой файл или неверная форма |
| 422 | `unknown_role`, `missing_file`, `empty_file`, `not_xlsx`, `bad_format` | Ошибка набора XLSX |
| 503 | `unavailable` | Сводка не найдена в кэше и не может быть создана без LLM |
| 500 | `internal_error` | Неожиданная ошибка; трассировка остаётся в серверном логе |

## Ограничения

Память сервиса не разделяется между несколькими процессами. После перезапуска идентификаторы старых прогонов и наборов недоступны. Для истории SKU из загруженного набора передавайте `run_id`; без него используется встроенный набор. Разворачивать эту версию следует одним процессом.
