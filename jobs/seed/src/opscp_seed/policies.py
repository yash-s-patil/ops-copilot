"""Puts the policy documents where the MCP server reads them."""

import contextlib
from collections.abc import Mapping
from pathlib import Path

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import ContainerClient

from opscp_common.documents import DocumentFormatError, parse_policy

_ENCODING = "utf-8"
_EXTENSION = ".md"
_MARKDOWN = ContentSettings(content_type=f"text/markdown; charset={_ENCODING}")


def read_policies(directory: Path) -> dict[str, str]:
    """The text of every policy in the directory, by name, each checked to be well formed."""
    if not directory.is_dir():
        raise NotADirectoryError(directory)
    texts: dict[str, str] = {}
    for path in sorted(directory.glob(f"*{_EXTENSION}")):
        try:
            text = path.read_text(encoding=_ENCODING)
            name = parse_policy(text).name
        except ValueError as error:
            raise DocumentFormatError(f"{path.name}: {error}") from error
        if name != path.stem:
            raise DocumentFormatError(f"{path.name} declares itself as {name}")
        texts[name] = text
    return texts


async def upload_policies(container: ContainerClient, texts: Mapping[str, str]) -> None:
    """Store each checked text under its policy's name, replacing what is there."""
    with contextlib.suppress(ResourceExistsError):
        await container.create_container()
    for name, text in texts.items():
        await container.upload_blob(
            f"{name}{_EXTENSION}",
            text.encode(_ENCODING),
            overwrite=True,
            content_settings=_MARKDOWN,
        )
