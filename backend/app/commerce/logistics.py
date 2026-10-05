"""Separate pickup of a whole import wave from delivery of one calculation."""

AIRPORT_NAMES = {
    "логистика рф", "логистика внутри рф", "вывоз из аэропорта",
    "логистика рф (вывоз из аэропорта)",
}
CLIENT_NAMES = {"доставка клиенту", "доставка клиенту в москве", "ндс доставки сдэк"}


def normalize_logistics_expenses(expenses: list[dict]) -> list[dict]:
    result = []
    for source in expenses:
        row = dict(source)
        stage = row.get("stage", "GENERAL")
        if stage == "GENERAL":
            name = " ".join(row.get("name", "").casefold().split())
            if name in AIRPORT_NAMES:
                stage = "DOMESTIC_LOGISTICS"
            elif name in CLIENT_NAMES or name.startswith("доставка сдэк "):
                stage = "CLIENT_DELIVERY"
        if stage in ("DOMESTIC_LOGISTICS", "CLIENT_DELIVERY"):
            row.update(stage=stage, method="BY_QUANTITY",
                       scope="WAVE" if stage == "DOMESTIC_LOGISTICS" else "REQUEST")
        result.append(row)
    return result
