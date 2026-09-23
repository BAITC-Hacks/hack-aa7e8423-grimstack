"""Офлайн-классификация SKU; тяжёлые зависимости нужны только при --model."""

import argparse
import csv
import re
from pathlib import Path

from app import ingest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "categories" / "sku_categories.csv"
MODEL = "convaiinnovations/laya-multilingual"
GROUPS = {
    "Автоматы и УЗО": "автоматические выключатели, УЗО, дифференциальные автоматы",
    "Кабель и провод": "электрический кабель, провода, шнуры",
    "Трубы и гофра": "трубы, гофра, кабель-каналы",
    "Розетки и выключатели": "розетки, выключатели, рамки",
    "Освещение": "лампы, светильники, прожекторы",
    "Контакторы и реле": "контакторы, пускатели, реле",
    "Щиты и корпуса": "электрощиты, боксы, корпуса",
    "Клеммы и шины": "клеммы, зажимы, шины",
    "Счётчики и измерение": "счётчики, измерительные приборы",
    "Крепёж": "крепёж, дюбели, саморезы",
    "Аксессуары": "прочие электротехнические аксессуары",
}
RULES = [
    (r"авдт|дифавтомат|узо|ва47|автоматич.*выкл", "Автоматы и УЗО"),
    (r"кабель|провод|шнур", "Кабель и провод"),
    (r"труба|гофра|кабель.?канал", "Трубы и гофра"),
    (r"розетк|выключател|рамка", "Розетки и выключатели"),
    (r"лампа|светильник|прожектор|led", "Освещение"),
    (r"контактор|пускател|реле", "Контакторы и реле"),
    (r"щит|корпус|бокс", "Щиты и корпуса"),
    (r"клемм|зажим|шина", "Клеммы и шины"),
    (r"счётчик|измерител", "Счётчики и измерение"),
    (r"дюбел|саморез|винт|болт", "Крепёж"),
]


def rule_group(name: str) -> str:
    lowered = name.lower()
    for pattern, group in RULES:
        if re.search(pattern, lowered):
            return group
    return "Аксессуары"


def main(use_model: bool, threshold: float):
    agent = None
    if use_model:
        try:
            import laya
        except ImportError as exc:
            raise SystemExit("Для --model установите laya отдельно: pip install laya") from exc
        agent = laya.load(MODEL)

    dataset = ingest.load_default()
    rows = []
    for (supplier, sku), item in dataset.skus.iterrows():
        name = str(item["name"])
        group, prob, method = rule_group(name), "", "rule"
        if agent:
            result = agent.predict({"name": name}, {
                "group": {"type": "choice", "instructions": "К какой товарной группе относится name?",
                          "criteria": GROUPS}})
            answer = result.get("answers", {}).get("group", {})
            candidate = answer.get("choice")
            confidence = answer.get("probability", answer.get("confidence"))
            if candidate in GROUPS and isinstance(confidence, (int, float)) and confidence >= threshold:
                group, prob, method = candidate, round(float(confidence), 4), "laya"
        rows.append({"supplier": supplier, "sku": sku, "group": group, "prob": prob, "method": method})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["supplier", "sku", "group", "prob", "method"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Записано {len(rows)} SKU в {OUT}; Laya: {'включена' if agent else 'не использовалась'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="store_true", help="Классифицировать через Laya с порогом уверенности")
    parser.add_argument("--threshold", type=float, default=0.7)
    args = parser.parse_args()
    main(args.model, args.threshold)
