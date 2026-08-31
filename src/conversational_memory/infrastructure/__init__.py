"""Concrete adapters for external storage and model details."""

from .sqlite import SQLiteMemoryRepository

__all__ = ["SQLiteMemoryRepository"]
