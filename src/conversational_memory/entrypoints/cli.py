"""Command-line boundary for the local memory-layer reference implementation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser without initializing any memory infrastructure."""
    return argparse.ArgumentParser(
        prog="conversational-memory",
        description="Local conversational memory layer",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and return a process exit code."""
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

