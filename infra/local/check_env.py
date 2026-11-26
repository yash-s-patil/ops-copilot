"""Stops with advice when .env was created before a setting that is needed now."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_QUOTES = "\"'"


def settings(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    pairs = (line.partition("=") for line in lines if "=" in line and not line.startswith("#"))
    return {name.strip(): value.strip().strip(_QUOTES) for name, _, value in pairs}


def missing(env: Path, example: Path) -> list[str]:
    """The settings the example lists that .env leaves out or leaves empty."""
    given = settings(env)
    return sorted(name for name in settings(example) if not given.get(name))


def main() -> int:
    absent = missing(ROOT / ".env", ROOT / ".env.example")
    if absent:
        sys.stderr.write(
            f"Your .env has no value for: {', '.join(absent)}.\n"
            "It was created before these were needed. Run `make dev-reset` to recreate it; "
            "this also deletes the local data volumes.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
