"""Minimal ordered SQLite migration runner."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from conversational_memory.application.errors import StorageError

_MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")


def initialize_schema(database_path: Path) -> None:
    """Bootstrap migration metadata and apply all numbered migrations atomically."""
    try:
        with closing(_connect(database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
            migrations = _load_migrations()
            _validate_migration_sequence(migrations)
            _reject_unknown_versions(connection, migrations)
            for version, checksum, script in migrations:
                _apply_migration(connection, version, checksum, script)
    except sqlite3.Error as error:
        raise StorageError("SQLite schema initialization failed") from error


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _load_migrations() -> list[tuple[int, str, str]]:
    migrations: list[tuple[int, str, str]] = []
    for path in sorted(_MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        raw = path.read_bytes()
        migrations.append(
            (
                int(path.name[:4]),
                hashlib.sha256(raw).hexdigest(),
                raw.decode("utf-8"),
            )
        )
    return migrations


def _validate_migration_sequence(migrations: list[tuple[int, str, str]]) -> None:
    versions = [version for version, _, _ in migrations]
    if not versions:
        raise StorageError("At least one numbered SQLite migration is required")
    if versions != list(range(1, len(versions) + 1)):
        raise StorageError("SQLite migrations must be contiguous beginning at 0001")


def _reject_unknown_versions(
    connection: sqlite3.Connection,
    migrations: list[tuple[int, str, str]],
) -> None:
    known_versions = {version for version, _, _ in migrations}
    applied_versions = {
        int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
    }
    if not applied_versions <= known_versions:
        raise StorageError("SQLite schema contains an unknown migration version")


def _apply_migration(
    connection: sqlite3.Connection,
    version: int,
    checksum: str,
    script: str,
) -> None:
    row = connection.execute(
        "SELECT checksum FROM schema_migrations WHERE version = ?",
        (version,),
    ).fetchone()
    if row is not None:
        if str(row[0]) != checksum:
            raise StorageError("SQLite migration checksum mismatch")
        return

    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in _iter_statements(script):
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
            (version, checksum, _utc_text(datetime.now(UTC))),
        )
        connection.commit()
    except (sqlite3.Error, StorageError):
        connection.rollback()
        raise


def _iter_statements(script: str) -> Iterator[str]:
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    if buffer.strip():
        raise StorageError("SQLite migration contains an incomplete statement")


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
