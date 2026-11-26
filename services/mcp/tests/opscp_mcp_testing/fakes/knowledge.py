"""An in-memory stand-in for the policy store."""

from collections.abc import Mapping
from pathlib import Path


class FakePolicies:
    def __init__(self, documents: Mapping[str, str]) -> None:
        self._documents = documents

    @classmethod
    def from_directory(cls, directory: Path) -> "FakePolicies":
        return cls({path.stem: path.read_text(encoding="utf-8") for path in directory.glob("*.md")})

    async def read(self, name: str) -> str | None:
        return self._documents.get(name)
