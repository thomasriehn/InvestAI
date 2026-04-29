"""Configuration loader."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


@dataclass
class Universe:
    base_currency: str = "CHF"
    stocks: list[str] = field(default_factory=list)
    indices: list[str] = field(default_factory=list)
    funds: list[str] = field(default_factory=list)
    fx_pairs: dict[str, str] = field(default_factory=dict)

    @property
    def all_tickers(self) -> list[str]:
        seen, out = set(), []
        for t in self.stocks + self.indices + self.funds:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def kind(self, ticker: str) -> str:
        if ticker in self.stocks:
            return "stock"
        if ticker in self.indices:
            return "index"
        if ticker in self.funds:
            return "fund"
        return "unknown"


@dataclass
class Settings:
    portfolio: dict[str, Any]
    forecast: dict[str, Any]
    observer: dict[str, Any]
    optimizer: dict[str, Any]


def load_universe(path: Path | None = None) -> Universe:
    p = path or DEFAULT_CONFIG_DIR / "universe.yaml"
    raw = yaml.safe_load(p.read_text())
    return Universe(
        base_currency=raw.get("base_currency", "CHF"),
        stocks=list(raw.get("stocks", []) or []),
        indices=list(raw.get("indices", []) or []),
        funds=list(raw.get("funds", []) or []),
        fx_pairs=dict(raw.get("fx_pairs", {}) or {}),
    )


def load_settings(path: Path | None = None) -> Settings:
    p = path or DEFAULT_CONFIG_DIR / "settings.yaml"
    raw = yaml.safe_load(p.read_text())
    return Settings(
        portfolio=raw.get("portfolio", {}),
        forecast=raw.get("forecast", {}),
        observer=raw.get("observer", {}),
        optimizer=raw.get("optimizer", {}),
    )
