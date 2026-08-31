from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

import pytest

from conversational_memory.application import StorageError
from conversational_memory.infrastructure.sqlite import migrations


def test_packaged_initial_migration_is_available() -> None:
    migration = files("conversational_memory.infrastructure.sqlite").joinpath(
        "migrations",
        "0001_initial.sql",
    )

    assert migration.is_file()
    assert "CREATE TABLE memories" in migration.read_text(encoding="utf-8")


def test_schema_initialization_fails_closed_without_numbered_migrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()
    monkeypatch.setattr(migrations, "_MIGRATIONS_DIRECTORY", migration_directory)
    database_path = tmp_path / "memory.sqlite3"

    with pytest.raises(StorageError, match="At least one numbered SQLite migration is required"):
        migrations.initialize_schema(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert tables == {"schema_migrations"}


@pytest.mark.parametrize("versions", [(2,), (1, 3)])
def test_schema_initialization_rejects_noncontiguous_numbered_migrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    versions: tuple[int, ...],
) -> None:
    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()
    for version in versions:
        (migration_directory / f"{version:04d}_test.sql").write_text(
            f"CREATE TABLE migration_{version} (value INTEGER);\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(migrations, "_MIGRATIONS_DIRECTORY", migration_directory)
    database_path = tmp_path / "memory.sqlite3"

    with pytest.raises(StorageError, match="contiguous beginning at 0001"):
        migrations.initialize_schema(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert tables == {"schema_migrations"}
