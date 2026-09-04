"""Gestion de la lista de clientes vigilados para la alerta de pedidos."""

from __future__ import annotations

import json
from pathlib import Path

WATCHLIST_FILE = Path(__file__).resolve().parent / "watchlist_clientes.json"


def load_watchlist(path: Path | str = WATCHLIST_FILE) -> list[str]:
    """Lee los nombres de clientes vigilados desde el fichero JSON."""
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    return list(data.get("clientes", []))


def save_watchlist(clientes: list[str], path: Path | str = WATCHLIST_FILE) -> None:
    path = Path(path)
    path.write_text(
        json.dumps({"clientes": clientes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_customer(name: str, path: Path | str = WATCHLIST_FILE) -> list[str]:
    name = name.strip()
    clientes = load_watchlist(path)
    if name and name not in clientes:
        clientes.append(name)
        save_watchlist(clientes, path)
    return clientes


def remove_customer(name: str, path: Path | str = WATCHLIST_FILE) -> list[str]:
    clientes = [c for c in load_watchlist(path) if c != name]
    save_watchlist(clientes, path)
    return clientes
