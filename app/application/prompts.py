"""Prompts versionados: el nombre del archivo es la versión que se registra en cada resultado."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass(frozen=True, slots=True)
class Prompt:
    version: str
    text: str


@lru_cache
def load_prompt(name: str) -> Prompt:
    text = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Prompt vacío: {name}")
    return Prompt(version=name.replace("_", "-"), text=text)
