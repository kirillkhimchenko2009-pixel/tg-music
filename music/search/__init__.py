"""Поиск: нормализация, параллельный опрос, дедупликация."""
from .engine import SearchEngine, engine
from .normalizer import NormalizedQuery, parse

__all__ = ["SearchEngine", "engine", "NormalizedQuery", "parse"]
