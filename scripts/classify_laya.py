"""Офлайн-классификация наименований по товарным группам: правила по ключевым словам + Laya.

Правила однозначно ловят сокращения 1С («ВА47», «Роз.», «Выкл»), Laya — описательные названия.
Ответ модели принимается только при уверенности ≥ LAYA_THRESHOLD, остальное уходит в «Прочее».
Заодно меряем качество Laya на товарах, где правила знают ответ, и пишем замер в laya_eval.json.

Запуск из корня репозитория (torch и laya ставятся только во временное окружение, не в requirements.txt):
    uv run --with-requirements requirements.txt --with "laya>=0.3.3" python scripts/classify_laya.py

Результат: data/categories/sku_categories.csv (supplier, sku, group, prob, source) — его читает
app/ingest при загрузке данных; data/categories/laya_eval.json — замер качества.
"""

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from laya import Router  # noqa: E402

from app import ingest  # noqa: E402
from app.ai.groups import GROUPS, LAYA_THRESHOLD, decide, keyword_group  # noqa: E402

OUT_DIR = ROOT / "data" / "categories"
QUESTION = {"group": {"type": "choice",
                      "instructions": "К какой товарной группе электротехники относится товар `product_name`?",
                      "criteria": GROUPS}}


def ask_laya(router: Router, name: str) -> tuple[str, float]:
    answer = router.predict({"product_name": name.strip()}, QUESTION)["answers"]["group"]
    return answer["choice"], float(answer["probabilities"][answer["choice"]])


def evaluate(rows: pd.DataFrame) -> dict:
    """Совпадение Laya с правилами на товарах, где правило дало ответ, — по порогам уверенности."""
    labeled = rows[rows["rule"].notna()]
    report = {"labeled_by_rules": int(len(labeled)), "thresholds": {}}
    for t in (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        kept = labeled[labeled["laya_prob"] >= t]
        report["thresholds"][str(t)] = {
            "coverage": round(len(kept) / len(labeled), 3) if len(labeled) else 0.0,
            "agreement": round(float((kept["laya"] == kept["rule"]).mean()), 3) if len(kept) else None,
        }
    return report


def laya_answers(names: pd.Series) -> pd.DataFrame:
    """Сырые ответы модели по (supplier, sku). Кэш в laya_raw.csv: смена порога не требует нового прогона."""
    raw_path = OUT_DIR / "laya_raw.csv"
    known = pd.read_csv(raw_path, dtype={"sku": str}) if raw_path.exists() else \
        pd.DataFrame(columns=["supplier", "sku", "laya", "laya_prob"])
    done = set(zip(known["supplier"], known["sku"]))
    todo = [(key, name) for key, name in names.items() if key not in done]
    if todo:
        router = Router(preload=False)
        fresh = [{"supplier": s, "sku": k, **dict(zip(("laya", "laya_prob"), ask_laya(router, str(name))))}
                 for (s, k), name in todo]
        known = pd.concat([known, pd.DataFrame(fresh)], ignore_index=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        known.round({"laya_prob": 4}).sort_values(["supplier", "sku"]).to_csv(raw_path, index=False)
    return known.set_index(["supplier", "sku"])


def main() -> None:
    names = ingest.load_default().skus["name"]
    started = time.time()
    answers = laya_answers(names)
    rows = pd.DataFrame({"name": names.map(lambda n: str(n).strip()), "rule": names.map(keyword_group)})
    rows = rows.join(answers).reset_index()

    decided = [decide(r.rule, r.laya, r.laya_prob) for r in rows.itertuples()]
    rows["group"], rows["prob"], rows["source"] = zip(*decided)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows[["supplier", "sku", "group", "prob", "source"]].round({"prob": 3}).sort_values(["supplier", "sku"]) \
        .to_csv(OUT_DIR / "sku_categories.csv", index=False)

    report = {
        "skus": int(len(rows)),
        "threshold": LAYA_THRESHOLD,
        "seconds": round(time.time() - started, 1),
        "source": dict(Counter(rows["source"])),
        "groups": dict(Counter(rows["group"]).most_common()),
        "laya_vs_rules": evaluate(rows),
        "note": ("Совпадение измерено на товарах с группой по правилам (правила сами не эталон). "
                 "Laya сообщает, что для choice с 11+ вариантами её уверенность не откалибрована, "
                 "поэтому порог взят по замеру, а не по номинальной вероятности."),
    }
    (OUT_DIR / "laya_eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
