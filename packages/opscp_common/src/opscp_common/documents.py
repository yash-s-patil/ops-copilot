"""Runbooks and policy documents: Markdown files with YAML front matter and `##` sections."""

import re
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, TypeAdapter

RUNBOOK_SECTIONS = ("symptoms", "checks", "resolution", "compensation", "escalation")
SUMMARY_MAX_CHARS = 160

_FRONT_MATTER = re.compile(r"\A---\n(?P<meta>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)
_HEADING_LINE = re.compile(r"^##[ \t]+(?P<text>\S[^\n]*)$", re.MULTILINE)
_HEADING_ID = re.compile(r"[a-z][a-z0-9-]*")
_ID_OPEN, _ID_CLOSE = "{#", "}"
_FRONT_MATTER_FIELDS = TypeAdapter(dict[str, Any])

Text = Annotated[str, Field(min_length=1)]
Summary = Annotated[str, Field(max_length=SUMMARY_MAX_CHARS, pattern=r"^\w[^\n]*$")]


class DocumentFormatError(ValueError):
    """The file is not laid out as front matter followed by the expected sections."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Section(_Model):
    id: Text
    title: Text
    content: Text


class PolicySection(Section):
    summary: Summary


class Runbook(_Model):
    id: Annotated[str, Field(pattern=r"^rb-[0-9]{3}$")]
    title: Text
    summary: Summary
    version: PositiveInt
    updated: date
    sections: tuple[Section, ...]


class PolicyDocument(_Model):
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
    version: PositiveInt
    effective_date: date
    sections: tuple[PolicySection, ...]


def _split(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise DocumentFormatError("the document must start with front matter between --- lines")
    try:
        loaded = yaml.safe_load(match["meta"])
    except yaml.YAMLError as error:
        raise DocumentFormatError(f"the front matter is not valid YAML: {error}") from error
    return _FRONT_MATTER_FIELDS.validate_python(loaded, strict=True), match["body"]


def _heading(text: str) -> tuple[str, str | None]:
    title, opened, rest = text.strip().partition(_ID_OPEN)
    if not opened:
        section_id = None
    elif rest.endswith(_ID_CLOSE) and _HEADING_ID.fullmatch(rest.removesuffix(_ID_CLOSE)):
        section_id = rest.removesuffix(_ID_CLOSE)
    else:
        raise DocumentFormatError(f"heading {text!r} has a malformed {{#id}}")
    title = title.strip()
    if not title or "{" in title or "}" in title:
        raise DocumentFormatError(f"heading {text!r} has no plain title")
    return title, section_id


def _headed_blocks(body: str) -> Iterator[tuple[str, str | None, str]]:
    headings = list(_HEADING_LINE.finditer(body))
    if not headings or body[: headings[0].start()].strip():
        raise DocumentFormatError("the body must be a series of sections that each start with ##")
    ends = [heading.start() for heading in headings[1:]] + [len(body)]
    for heading, end in zip(headings, ends, strict=True):
        title, section_id = _heading(heading["text"])
        yield title, section_id, body[heading.end() : end].strip()


def parse_runbook(text: str) -> Runbook:
    """A runbook has exactly the five sections of RUNBOOK_SECTIONS, in that order."""
    meta, body = _split(text)
    sections = tuple(
        Section(id=title.lower(), title=title, content=content)
        for title, _, content in _headed_blocks(body)
    )
    found = tuple(section.id for section in sections)
    if found != RUNBOOK_SECTIONS:
        raise DocumentFormatError(f"expected sections {RUNBOOK_SECTIONS}, found {found}")
    return Runbook.model_validate({"sections": sections} | meta)


def parse_policy(text: str) -> PolicyDocument:
    """Each policy section carries a stable id, `## Title {#id}`, and opens with its summary."""
    meta, body = _split(text)
    sections: list[PolicySection] = []
    for title, section_id, content in _headed_blocks(body):
        if section_id is None:
            raise DocumentFormatError(f"section {title!r} has no {{#id}}")
        summary, _, rest = content.partition("\n")
        if rest and not rest.startswith("\n"):
            raise DocumentFormatError(f"section {section_id!r} must open with a one-line summary")
        sections.append(PolicySection(id=section_id, title=title, content=content, summary=summary))
    ids = [section.id for section in sections]
    if len(set(ids)) != len(ids):
        raise DocumentFormatError(f"section ids must be unique, found {ids}")
    return PolicyDocument.model_validate({"sections": tuple(sections)} | meta)


def _load[Document: Runbook | PolicyDocument](
    directory: Path, parse: Callable[[str], Document], name: Callable[[Document], str]
) -> tuple[Document, ...]:
    if not directory.is_dir():
        raise NotADirectoryError(directory)
    documents: list[Document] = []
    for path in sorted(directory.glob("*.md")):
        try:
            document = parse(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise DocumentFormatError(f"{path.name}: {error}") from error
        if name(document) != path.stem:
            raise DocumentFormatError(f"{path.name} declares itself as {name(document)}")
        documents.append(document)
    return tuple(documents)


def load_runbooks(directory: Path) -> tuple[Runbook, ...]:
    """The runbooks directly inside the directory; subfolders are a separate collection."""
    return _load(directory, parse_runbook, lambda runbook: runbook.id)


def load_policies(directory: Path) -> tuple[PolicyDocument, ...]:
    return _load(directory, parse_policy, lambda policy: policy.name)
