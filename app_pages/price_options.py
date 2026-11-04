"""Display ordering only: identifiers and stored selections remain unchanged."""
from __future__ import annotations

from collections.abc import Callable, Iterable
import re
from typing import TypeVar
import unicodedata

import pandas as pd

T = TypeVar("T")


def natural_key(value: object) -> tuple:
    text = "" if value is None or pd.isna(value) else str(value)
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                     if not unicodedata.combining(c))
    return tuple((1, int(part)) if part.isdigit() else (0, part) for part in re.split(r"(\d+)", folded))


def ordered(values: Iterable[T], label: Callable[[T], object] = str) -> list[T]:
    return sorted(values, key=lambda value: (natural_key(label(value)), natural_key(value), str(value)))


def product_eans(frame: pd.DataFrame, eans: Iterable[str] | None = None) -> list[str]:
    records = {row["ean"]: row for row in frame.to_dict("records")}
    values = records if eans is None else dict.fromkeys(eans)

    def key(ean: str) -> tuple:
        row = records.get(ean, {})
        return tuple(natural_key(row.get(field, "")) for field in ("brand", "model", "color")) + (
            natural_key(ean), str(ean))

    return sorted(values, key=key)
