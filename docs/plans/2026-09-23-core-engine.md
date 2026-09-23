# Ядро расчёта (ingest + engine): план реализации

План замены заглушек загрузки и расчёта на конвейер обработки реальных выгрузок IEK и SE.

**Статус:** исходный план реализации от 23 сентября 2026 года. Чекбоксы и требования
сохранены как часть плана и не отражают текущую готовность кода.

## Содержание

- [Контекст и цель](#контекст-и-цель)
- [Глобальные ограничения](#глобальные-ограничения)
- [Структура файлов](#структура-файлов)
- [Задача 1: Dataset, общие помощники, конфиг, фабрика, приёмка (координатор)](#задача-1-dataset-общие-помощники-конфиг-фабрика-приёмка-координатор)
- [Задача 2: загрузчик IEK](#задача-2-загрузчик-iek)
- [Задача 3: загрузчик SE](#задача-3-загрузчик-se)
- [Задача 4: очистка и stockout](#задача-4-очистка-и-stockout)
- [Задача 5: прогноз, политика, объяснение, сборка результата](#задача-5-прогноз-политика-объяснение-сборка-результата)
- [Задача 6: интеграция на реальных данных (координатор)](#задача-6-интеграция-на-реальных-данных-координатор)
- [Порядок исполнения](#порядок-исполнения)

## Контекст и цель

Для исполнителей-агентов: задачи выполняются по TDD. Шаги — чекбоксы `- [ ]`.
Алгоритм задан формулами в `docs/design.md` §4. Правила разбора файлов —
в `docs/data-profile.md` §1–2, §4. План фиксирует файлы, интерфейсы,
тесты и команды. Реализацию пишет исполнитель строго по этим формулам.

**Цель.** Заменить заглушки `app/ingest` и `app/engine` на настоящий расчёт: реальные
выгрузки IEK и SE → `RunResult` по контракту `app/contracts.py`. Должны пройти приёмка
must-have и smoke-тест на реальных данных.

**Архитектура.**

- `ingest` приводит 6 xlsx поставщика к `Dataset` — набору канонических pandas-таблиц.
- `engine` прогоняет `Dataset` через конвейер
  `cleaning → stockout → forecast → policy → explain` и собирает `RunResult`.
- `baseline` — Excel-метод на той же политике.

**Стек.** Python 3.12, pandas 3, numpy, openpyxl, pydantic 2, pytest. Окружение:
`uv venv --python 3.12 .venv && uv pip install -r requirements.txt`.
Команды выполнять в корне репозитория, в терминале zsh/bash на macOS или WSL/Ubuntu на Windows.

## Глобальные ограничения

- Контракт `app/contracts.py` и сигнатуры `engine.run/history/meta`,
  `ingest.load_default/load_uploaded` заморожены.
- Файлы владельца А: `app/ingest/**`, `app/engine/**`, `app/ai/**`, `data/**`,
  `tests/engine/**`, `docs/**`. Чужие файлы не трогать: `app/main.py`, `app/api/**`,
  `app/store.py`, `requirements.txt`, `Dockerfile`, `frontend/**`, `README.md`.
- Новых зависимостей не добавлять: `requirements.txt` принадлежит Б.
- Дата расчёта `as_of = 2026-09-22`. Закрытые месяцы — `2024-01…2026-08`.
  Сентябрь 2026 неполный и в статистику не идёт.
- Дефолты поставщиков: IEK — L = 24, R = 7; SE — L = 40, R = 30.
- Уровни сервиса: A / «Кат. 1» → 0.97; B / «Кат. 2» / «Кат. 5» → 0.95; C / «Кат. 3» → 0.90.
- Код 1С нормализуется только `str(x).strip()`. `250600007` и `250600007_` — разные SKU.
- Номер документа в выход попадает только хэшем (`sha1(str)[:10]`).
- Тексты для пользователя — на русском.
- Исполнители не коммитят. Коммит делает координатор после проверки.

## Структура файлов

| Файл | Ответственность | Задача |
| :-- | :-- | :-- |
| `app/ingest/dataset.py` | `Dataset` + `concat()` | 1 |
| `app/ingest/common.py` | месяцы из заголовков 1С, код, хэш документа, чтение листа | 1 |
| `app/ingest/iek.py` | `load(folder, as_of) -> Dataset` для IEK | 2 |
| `app/ingest/se.py` | `load(folder, as_of) -> Dataset` для SE | 3 |
| `app/ingest/__init__.py` | `load_default`, `load_uploaded` (валидация уже есть) | 2 |
| `app/engine/config.py` | дефолты поставщиков, уровни сервиса, пороги | 1 |
| `app/engine/cleaning.py` | паллетный поток SE, разовые строки → события и помесячный излишек | 4 |
| `app/engine/stockout.py` | типы stockout-месяцев, доля дней в наличии, добавка спроса | 4 |
| `app/engine/forecast.py` | сезонные профили, тренд, сегмент, база, прогноз, σ | 5 |
| `app/engine/policy.py` | S, SS, net, кратность, срочность, флаги | 5 |
| `app/engine/explain.py` | `components` + `explanation` | 5 |
| `app/engine/pipeline.py` | `analyze()`, `baseline()`, `history()`, `meta()` | 5 |
| `app/engine/__init__.py` | `run/history/meta` → `pipeline` | 5 |
| `data/raw/<iek\|se>/<role>.xlsx` | выгрузки партнёра | 1 |
| `tests/engine/factory.py` | синтетический `Dataset` | 1 |
| `tests/engine/test_acceptance.py` | 5 must-have | 1 |
| `tests/engine/test_units.py` | cleaning, stockout | 4 |
| `tests/engine/test_ingest_real.py` | загрузка реальных файлов | 2, 3 |
| `tests/engine/test_real_data.py` | smoke всего расчёта + демо-SKU | 6 |

## Задача 1: `Dataset`, общие помощники, конфиг, фабрика, приёмка (координатор)

**Файлы:** `app/ingest/dataset.py`, `app/ingest/common.py`, `app/engine/config.py`,
`tests/engine/factory.py`, `tests/engine/test_acceptance.py`,
`data/raw/<iek|se>/<role>.xlsx`.

**Производит.** Эти определения используют все остальные задачи:

```python
# app/ingest/dataset.py
@dataclass
class Dataset:
    as_of: date
    skus: pd.DataFrame           # index (supplier, sku); cols: article, name, unit, category,
                                 #   group4, moq (float, 0 = не заказывать), unit_cost (float|NaN),
                                 #   discontinued, keep_1m, new_item (bool), product_group (str|None)
    sales_monthly: pd.DataFrame  # index (supplier, sku); cols pd.Period('2024-01','M') … месяц as_of
    stock_monthly: pd.DataFrame  # тот же индекс и колонки; остаток на 1-е число, ≥ 0
    sales_tx: pd.DataFrame       # cols: supplier, sku, date (datetime64), doc (str), qty (float > 0)
    stock_now: pd.DataFrame      # index (supplier, sku); cols: qty (≥0), source ('warehouses'|'estimate_lower_bound')
    in_transit: pd.DataFrame     # cols: supplier, sku, qty (> 0), eta (datetime64)

def concat(parts: list[Dataset]) -> Dataset

# app/ingest/common.py
def month_period(label) -> pd.Period | None  # 'янв. 2024', 'сент. 2026', 'Январь 2024 г.' → Period; иначе None
def norm_code(x) -> str | None              # str(x).strip(); '', 'nan', 'Итого' → None
def doc_hash(doc: str) -> str               # sha1(doc)[:10]
def read_sheet(path, sheet=0) -> pd.DataFrame  # header=None, dtype=object

# app/engine/config.py
SUPPLIERS = {"IEK": {"name": "IEK (ИЭК)", "lead_time_days": 24, "review_period_days": 7},
             "SE": {"name": "Systeme Electric", "lead_time_days": 40, "review_period_days": 30}}
SERVICE_LEVEL = {"A": .97, "B": .95, "C": .90, "Кат. 1": .97, "Кат. 2": .95, "Кат. 3": .90, "Кат. 5": .95}
DO_NOT_ORDER_CATEGORIES = {"Кат. 7"}

# tests/engine/factory.py
def noisy(level, n=33, amp=0.15) -> list[float]                     # детерминированный шум вокруг level
def seasonal(level, pattern12, n=33) -> list[float]                  # месяц i → level·pattern[i % 12]
def make_dataset(series: dict[str, list[float]], *, supplier="IEK", stock=None, stock_now=50.0,
                 in_transit=0.0, moq=1.0, category="B", unit_cost=None, tx_lines=5) -> Dataset
    # 33 месяца 2024-01…2026-09; stock по умолчанию — большой ровный остаток (без stockout);
    # sales_tx для 2025+ = tx_lines равных строк в месяц (дни 3, 8, 13, 18, 23)
def add_oneoff(ds, sku, day: date, qty) -> None   # строка в sales_tx и +qty в sales_monthly того же месяца
```

**Тесты приёмки** (`tests/engine/test_acceptance.py`) — пять must-have из `docs/design.md` §9:

1. В пути уменьшает заказ. Остаток, прирост, категория и история меняют результат.
2. Сезонный профиль: прогноз на октябрь больше, чем на декабрь, в 1.5 раза и выше;
   расхождение с плоским средним больше 20 %.
3. Stockout: заказ выше, чем при тех же продажах без stockout; флаг `stockout_restored`.
4. Разовая строка ×100 от типичной: заказ меняется не больше чем на 10 %,
   флаг `oneoff_excluded`. `baseline` при этом раздувается больше чем на 50 %.
5. Два поставщика: строки сгруппированы, у каждой есть объяснение, «водопад» сходится,
   итог кратен `moq`.

- [ ] Скопировать выгрузки в `data/raw/iek/*.xlsx`, `data/raw/se/*.xlsx` под ролями.
- [ ] Написать `dataset.py`, `common.py`, `config.py`, `factory.py`, `test_acceptance.py`.
- [ ] `pytest tests/engine/test_acceptance.py` → FAIL: заглушка `engine.run` ещё отдаёт моки.
- [ ] Коммит.

## Задача 2: загрузчик IEK

**Файлы:** `app/ingest/iek.py`, `app/ingest/__init__.py` (`load_default`, путь данных),
`tests/engine/test_ingest_real.py` (часть IEK).

**Использует:** `Dataset`, `common.*`. **Производит:** `iek.load(folder: Path, as_of: date) -> Dataset`.

Правила — `docs/data-profile.md` §1, §2, §4.

- **Роли и источники:**
  - `monthly_sales` — заголовок в строках 0–1, строка «Итого» и колонка «Итого» отбрасываются;
  - `monthly_stock` — заголовок в строках 0–2; колонку «Итого» отбросить, отрицательные значения → 0;
  - `sales_tx` — брать только «Расходная накладная», qty > 0, дата ≥ 2025-01-01; `doc = doc_hash(Документ)`;
  - `in_transit` — колонки 3–8, ETA из заголовка «поступление до ДД.ММ.ГГГГ»; служебные строки
    и дубли выкинуть;
  - `moq` — «Мин. разр. к отгр.»; если пусто — упаковка из названия `(N)` / `(N/M)` → N,
    иначе 1.
- **Справочник SKU:**
  - `category` = ABC по количеству продаж за 12 закрытых месяцев, границы 80/95 %;
  - `unit_cost` = NaN;
  - `discontinued`, если в названии есть `!!!`;
  - `keep_1m`, если в «Пути» есть «поддерживаем склад»;
  - `new_item` — SKU из секции «НОВИНКИ» «Пути»;
  - `group4 = sku[:4]`, если код вида `\d{9}_`.
- **Текущий остаток:** `stock_now = max(0, остаток на 01.09 − продажи сентября)`,
  источник `estimate_lower_bound`.

- [ ] Тест: `load(data/raw/iek)` → в `sales_monthly` около 2 460 SKU, колонки с `2024-01`
      по `2026-09`, пропусков нет.
- [ ] Тест: в пути 109 517 ед. у 300 SKU (±1 %).
- [ ] Тест: `010500006_` — артикул `MVA20-1-016-C`, `moq == 12`.
- [ ] Тест: в `sales_tx` есть строка 210 000 шт по `130200305_`.
- [ ] Прогнать → FAIL, реализовать → PASS.
- [ ] `load_default()` = `concat([iek.load(...), se.load(...)])`. Если `se.py` ещё нет,
      грузить только IEK. Группы Laya подмешиваются из
      `data/categories/sku_categories.csv`, если файл есть.
- [ ] `load_uploaded`: после валидации записать байты во временную папку `<role>.xlsx`
      и вызвать `<supplier>.load()`. Ошибки разбора → `IngestError("bad_format", …, role)`.

## Задача 3: загрузчик SE

**Файлы:** `app/ingest/se.py`, `tests/engine/test_ingest_real.py` (часть SE).

**Производит:** `se.load(folder: Path, as_of: date) -> Dataset`.

- **Роли и источники:**
  - `monthly_sales` — «Кратность» берётся из файла MOQ;
  - `monthly_stock` — строка 0 заголовок, строки 1–2 пустые, первая колонка `№`;
  - `sales_tx` — те же правила, что у IEK;
  - `in_transit` (TDSheet) — строка 0 надзаголовок, строка 1 заголовки;
  - `moq` — «Кратность»; 0 означает «не заказывать».
- **Текущий остаток:** `stock_now` = Свободный остаток + Витрина + Остаток ТЗ + РЦ ЕКТ +
  Розничный склад, источник `warehouses`. Для SKU, которых нет в TDSheet, — остаток на
  01.09 минус продажи сентября, источник `estimate_lower_bound`.
- **В пути:** колонка «СЭ в пути 24.09», ETA = 2026-09-24.
- **Справочник:**
  - `category = "Кат. " + Категория 2026`;
  - `unit_cost` = «СС реал»;
  - `discontinued`, если в названии `!!!`;
  - `new_item` — категория 5.
- **История:** 22 SKU, которых нет в помесячном файле, дополнить помесячными продажами
  из TDSheet.

- [ ] Тест: около 554 SKU продаж; в пути 50 160 шт у 7 SKU.
- [ ] Тест: `030200193_` — `moq == 3780`, `unit_cost ≈ 133.71`, `stock_now.qty == 40798`.
- [ ] Тест: по `300200428_` остаток 1 174.
- [ ] Прогнать → FAIL, реализовать → PASS.

## Задача 4: очистка и stockout

**Файлы:** `app/engine/cleaning.py`, `app/engine/stockout.py`, `tests/engine/test_units.py`.

**Производит:**

```python
# cleaning.py
def pallet_mask(tx: pd.DataFrame, skus: pd.DataFrame) -> pd.Series
    # bool по строкам tx: SE, moq ≥ 50, qty ≥ moq, qty % moq == 0
def detect_oneoffs(tx: pd.DataFrame, skus: pd.DataFrame, sales_monthly: pd.DataFrame) -> pd.DataFrame
    # → events: supplier, sku, date, doc, month (Period), qty, capped_to, excess, project (bool)
def monthly_excess(events: pd.DataFrame, like: pd.DataFrame) -> pd.DataFrame
    # сумма excess на (supplier, sku) × месяц, форма как у like, нули вне событий

# stockout.py
def restore(cleaned: pd.DataFrame, stock_monthly: pd.DataFrame, tx: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]
    # cleaned — закрытые месяцы → (added ≥ 0 той же формы,
    #                              types той же формы: None|'full'|'start'|'end'|'effective')
```

**Правило разовой строки** (`docs/design.md` §4 п. 2). Статистика SKU считается по строкам без
паллетов; нужно ≥ 5 строк, у SE ≥ 20. Строка отмечается, если выполнено (B) или (U).

- (B) — все условия одновременно:
  - `q > 5·медиана`;
  - `q > Q3 + 3·IQR`;
  - `q ≥ медиана ненулевых закрытых месяцев SKU` (у SE — `≥ среднего`);
  - отмеченные по B строки у SKU встречаются не больше чем в 3 месяцах, у SE — в 4.
    Если месяцев больше, у SKU ничего не отмечается: это регулярный опт.
- (U) — `q ≥ 2 ×` вторая по величине строка **и** `q > 5·медиана`.

`capped_to` = медиана строк SKU, `excess = qty − capped_to`. Документ с ≥ 3 отмеченными
строками получает `project = True`.

**Stockout** (`docs/design.md` §4 п. 3):

- `open = S[m]`, `close = S[m+1]`; тип месяца `full` / `start` / `end` / `effective`
  (`open < 0.25·база`);
- SKU активен, если продавался в 6 месяцах до и в 6 месяцах после;
- база — медиана 6 предыдущих месяцев в наличии, таких месяцев ≥ 3;
- `added = min(база·(1 − доля), 2·база)`;
- доля дней в наличии для 2025+ считается по транзакциям:
  - `end` — рабочие дни (пн–сб) до даты, когда накопленные продажи месяца достигли
    `open`, делённые на рабочие дни месяца;
  - `start` — рабочие дни от первой продажи до конца месяца, делённые на рабочие дни месяца;
  - `full` — 0;
- для 2024 доля по умолчанию: `full` = 0, `start` и `end` = 0.5;
- `effective` — доля 0.5.

- [ ] Тесты:
  - строка 2000 при типичной 20 отмечается, `capped_to == 20`;
  - SKU, у которого крупные строки в 6 месяцах, не отмечается;
  - паллеты SE не отмечаются;
  - месяц `full` получает `added ≈ база`;
  - месяц без stockout получает 0.
- [ ] FAIL → реализация → PASS.

## Задача 5: прогноз, политика, объяснение, сборка результата

**Файлы:** `app/engine/forecast.py`, `app/engine/policy.py`, `app/engine/explain.py`,
`app/engine/pipeline.py`, `app/engine/__init__.py`.

**Использует:** задачу 4 (`detect_oneoffs`, `monthly_excess`, `restore`). Пока её нет,
вызывать через `try/except ImportError` с нулевыми поправками — так приёмка 1, 2, 5
проходит раньше.

**Производит:** `engine.run(data, params) -> RunResult`,
`engine.history(data, supplier, sku, params) -> SkuHistory`, `engine.meta(data) -> Meta`.

**Конвейер `analyze`:**

- `raw` = `sales_monthly` за закрытые месяцы, отрицательные значения → 0;
- `cleaned = max(0, raw − excess)`;
- `demand = cleaned + added`;
- сегмент по Syntetos–Boylan: ADI 1.32 / CV² 0.49 за 12 месяцев;
- сезонный профиль по `group4` (в группе ≥ 5 SKU), затем `product_group`, затем поставщик;
  смесь `0.3·свой + 0.7·группа` при ≥ 24 ненулевых месяцах и корреляции 2024↔2025 ≥ 0.6;
  профиль нормирован к среднему 1;
- база — среднее `demand / season` за 12 закрытых месяцев;
- тренд — сумма последних 6 месяцев к тем же месяцам год назад, ограничен [0.67; 1.5];
  сжатие `n/(n+6)`; если в каком-то окне сумма < 6, тренд = 1; у intermittent/lumpy тренд = 1;
- `forecast(m) = база·season(m)·T·(1 + growth/100)` для 3 месяцев после месяца `as_of`
  и для остатка текущего месяца;
- спрос на горизонт: сумма прогноза по дням от `as_of` на `L + R` дней, помесячно
  пропорционально;
- σ — стандартное отклонение `demand` за 12 месяцев;
- `SS = z·σ·√((L+R)/30)`, z = `NormalDist().inv_cdf(SL)` из stdlib `statistics`;
- `S = horizon + SS`;
- `net = S − stock_now − in_transit(ETA ≤ as_of + L + R)`; `qty = ceil(net/moq)·moq`;
- intermittent/lumpy: `S = max(S, медианная строка)`;
- не заказывать (qty = 0, флаг `do_not_order`): `discontinued`, категория «Кат. 7»,
  `moq == 0`, нет продаж 12 месяцев;
- `keep_1m`: горизонт ограничен 30 днями;
- срочность — `docs/design.md` §4 п. 7;
- флаги: `seasonal` (размах профиля ≥ 1.5), `trend_up` (T ≥ 1.05), `trend_down` (T ≤ 0.95),
  `intermittent`, `overstock`, `approx_stock`, `oneoff_excluded`, `project_order`,
  `stockout_restored`, `discontinued`, `keep_1m`, `new_item`, `no_history`.

**`explain`:**

- компоненты по контракту; qty-компоненты в сумме строго равны `recommended_qty`:
  `horizon_demand`, `safety_stock`, `−stock`, `−in_transit`, `moq_rounding`; если `net ≤ 0`,
  то `moq_rounding = −(horizon + SS − stock − transit)`;
- `explanation` — одна строка по-русски: главная причина (сезон, тренд, stockout, разовый
  заказ, в пути), прогноз в месяц и итог с кратностью.

**`baseline`:** демонстрирует разницу с Excel-подходом.

- спрос = среднее сырых продаж за 12 закрытых месяцев, без очистки, восстановления,
  сезона и тренда;
- σ — по сырым;
- та же политика и округление;
- `explanation` = «Excel-метод: среднее 12 мес × …».

`analyze` заполняет `baseline_qty` значением `baseline` для той же строки.

**`RunResult`:**

- в `lines` попадают строки с `recommended_qty > 0` или `urgency != "none"`, сортировка
  по срочности, затем по сумме или количеству;
- фильтры `params.supplier`, `params.category`;
- L, R и уровень сервиса берутся из `params`, если заданы;
- в `kpi` считаются исключённое, восстановленное (за 12 мес) и излишки;
- `warnings`: IEK-остаток — оценка; сколько SKU пропущено как «не заказывать».

**`history`:** ряды `raw` / `cleaned` / `restored` / `stockout` по закрытым месяцам,
`forecast` на 3 месяца, события разовых строк.

**`meta`:** `SUPPLIERS`, категории из `skus`, `product_groups`,
`llm_available = bool(os.getenv("OPENAI_API_KEY")) or samples/cache не пуст`.

- [ ] `pytest tests/engine/test_acceptance.py` — PASS (MH3, MH4 после задачи 4).
- [ ] `pytest tests/test_contracts.py` остаётся зелёным.

## Задача 6: интеграция на реальных данных (координатор)

**Файл:** `tests/engine/test_real_data.py`.

- [ ] `ds = ingest.load_default()`; `engine.run(ds, RunParams())` отрабатывает меньше чем
      за 30 с, без NaN, все количества ≥ 0 и кратны `moq`, у каждой строки есть объяснение.
- [ ] Демо-SKU (`docs/design.md` §5) получают ожидаемые флаги:
  - `130200305_` → разовая строка исключена;
  - `010300096_` → `stockout_restored`;
  - `130300792_` → `seasonal`;
  - `030200201_` → `trend_up`.
- [ ] `baseline` отрабатывает по обоим поставщикам.
- [ ] Перегенерировать `contracts/sample_*.json` из реального прогона (тест контракта
      должен оставаться зелёным) и закоммитить.

## Порядок исполнения

1. Задача 1 — координатор, сразу.
2. Задачи 2, 3, 4, 5 — параллельно, 4 агента на Sonnet в одном рабочем дереве с
   непересекающимися файлами. Исполнители не коммитят.
3. Задача 6 — координатор: проверка, правки стыков, коммит.
