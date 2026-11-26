"""Keyword search over the runbook files, for running without Azure AI Search."""

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from opscp_common.documents import Runbook, load_runbooks
from opscp_mcp.ports.knowledge import CHAOS, STANDARD, Collection, RunbookChunk, RunbookEntry

CHAOS_FOLDER = "chaos"
FIRST_CHAOS_NUMBER = 900

_WORD = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "if",
        "in",
        "is",
        "it",
        "its",
        "not",
        "of",
        "on",
        "or",
        "that",
        "the",
        "then",
        "this",
        "to",
        "was",
        "when",
        "with",
    ]
)
_ID_PREFIX = "rb-"
_TITLE_WEIGHT = 2.0
_TERM_SATURATION = 1.5
_LENGTH_NORMALISATION = 0.75
_SCORE_DECIMALS = 4


def _words(text: str) -> list[str]:
    return [word for word in _WORD.findall(text.lower()) if word not in _STOP_WORDS]


@dataclass(frozen=True, slots=True)
class _Chunk:
    section: str
    content: str
    terms: Mapping[str, int]
    length: int


@dataclass(frozen=True, slots=True)
class _Indexed:
    collection: Collection
    runbook_id: str
    title: str
    title_words: frozenset[str]
    chunks: tuple[_Chunk, ...]


def _indexed(collection: Collection, runbooks: Iterable[Runbook]) -> list[_Indexed]:
    indexed: list[_Indexed] = []
    for runbook in runbooks:
        in_chaos_range = int(runbook.id.removeprefix(_ID_PREFIX)) >= FIRST_CHAOS_NUMBER
        if in_chaos_range != (collection == CHAOS):
            raise ValueError(f"{runbook.id} does not belong in the {collection} collection")
        chunks: list[_Chunk] = []
        for section in runbook.sections:
            words = _words(section.content)
            chunks.append(
                _Chunk(
                    section=section.id,
                    content=section.content,
                    terms=Counter(words),
                    length=len(words),
                )
            )
        indexed.append(
            _Indexed(
                collection=collection,
                runbook_id=runbook.id,
                title=runbook.title,
                title_words=frozenset(_words(runbook.title)),
                chunks=tuple(chunks),
            )
        )
    return indexed


class InMemoryRunbooks:
    """Okapi BM25 over runbook sections, plus a bonus for query words in the runbook's title.

    The section returned for a runbook is the one whose own text matches best; the title
    decides how runbooks rank against each other, not which section represents them.
    Runbooks numbered 900 and above are the chaos collection and are refused anywhere else.
    """

    def __init__(self, standard: Iterable[Runbook], chaos: Iterable[Runbook] = ()) -> None:
        ordered = sorted(standard, key=lambda runbook: runbook.id)
        self._runbooks = _indexed(STANDARD, ordered) + _indexed(CHAOS, chaos)
        self._catalog = tuple(
            RunbookEntry(runbook_id=runbook.id, title=runbook.title, summary=runbook.summary)
            for runbook in ordered
        )

    @classmethod
    def from_directory(cls, directory: Path) -> "InMemoryRunbooks":
        """Standard runbooks sit in the directory; the chaos collection in its own subfolder."""
        chaos = directory / CHAOS_FOLDER
        return cls(load_runbooks(directory), load_runbooks(chaos) if chaos.is_dir() else ())

    async def search(
        self, query: str, *, limit: int, collections: frozenset[Collection]
    ) -> tuple[RunbookChunk, ...]:
        searched = [runbook for runbook in self._runbooks if runbook.collection in collections]
        chunks = [chunk for runbook in searched for chunk in runbook.chunks]
        wanted = set(_words(query))
        average_length = sum(chunk.length for chunk in chunks) / len(chunks) if chunks else 0
        if not wanted or not average_length:
            return ()
        rarity = {
            word: _rarity(sum(word in chunk.terms for chunk in chunks), len(chunks))
            for word in wanted
        }
        found = [
            scored
            for runbook in searched
            if (scored := _best_section(runbook, rarity, average_length)) is not None
        ]
        found.sort(key=lambda chunk: (-chunk.score, chunk.runbook_id))
        return tuple(found[:limit])

    async def catalog(self) -> tuple[RunbookEntry, ...]:
        return self._catalog


def _best_section(
    runbook: _Indexed, rarity: Mapping[str, float], average_length: float
) -> RunbookChunk | None:
    scores = [_content_score(chunk, rarity, average_length) for chunk in runbook.chunks]
    best = max(range(len(scores)), key=lambda index: (scores[index], -index))
    title_bonus = _TITLE_WEIGHT * sum(
        rarity[word] for word in rarity if word in runbook.title_words
    )
    score = scores[best] + title_bonus
    if score <= 0:
        return None
    chunk = runbook.chunks[best]
    return RunbookChunk(
        runbook_id=runbook.runbook_id,
        title=runbook.title,
        section=chunk.section,
        content=chunk.content,
        score=round(score, _SCORE_DECIMALS),
    )


def _content_score(chunk: _Chunk, rarity: Mapping[str, float], average_length: float) -> float:
    relative_length = chunk.length / average_length
    return sum(
        weight * _saturated(chunk.terms.get(word, 0), relative_length)
        for word, weight in rarity.items()
    )


def _rarity(containing: int, total: int) -> float:
    return math.log(1 + (total - containing + 0.5) / (containing + 0.5))


def _saturated(occurrences: int, relative_length: float) -> float:
    normalised = _TERM_SATURATION * (
        1 - _LENGTH_NORMALISATION + _LENGTH_NORMALISATION * relative_length
    )
    return occurrences * (_TERM_SATURATION + 1) / (occurrences + normalised)
