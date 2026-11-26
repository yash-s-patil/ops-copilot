from datetime import date
from typing import Literal, get_args

import structlog

from opscp_common.documents import PolicyDocument, parse_policy
from opscp_common.errors import InternalError, NotFoundError, ValidationFailedError
from opscp_common.limits import Limits
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.ports.knowledge import Policies
from opscp_mcp.services.caps import Result, cap_text

type PolicyName = Literal["refund-policy", "shipping-sla", "premium-customer-handling"]

POLICY_NAMES: tuple[PolicyName, ...] = get_args(PolicyName.__value__)

_LOG = structlog.get_logger(__name__)


class SectionOutline(Result):
    id: str
    title: str
    summary: str


class SectionText(Result):
    id: str
    title: str
    content: str
    truncated: bool


class PolicyView(Result):
    """The outline of a document, or one of its sections. Never the whole text."""

    name: PolicyName
    version: int
    effective_date: date
    sections: tuple[SectionOutline, ...] | None
    section: SectionText | None


class PolicyService:
    def __init__(self, policies: Policies, limits: Limits) -> None:
        self._policies = policies
        self._limits = limits

    async def get(self, identity: Identity, name: str, *, section: str | None = None) -> PolicyView:
        identity.require(Permission.POLICIES_READ)
        document = await self._document(_known(name))
        if section is None:
            outline = tuple(
                SectionOutline(id=part.id, title=part.title, summary=part.summary)
                for part in document.sections
            )
            return _view(document, sections=outline, section=None)
        return _view(document, sections=None, section=self._section(document, section))

    async def _document(self, name: PolicyName) -> PolicyDocument:
        text = await self._policies.read(name)
        if text is None:
            _LOG.error("policy_document_missing", policy=name)
            raise InternalError
        try:
            document = parse_policy(text)
        except ValueError as error:
            _LOG.error("policy_document_unreadable", policy=name)
            raise InternalError from error
        if document.name != name:
            _LOG.error("policy_document_misfiled", policy=name)
            raise InternalError
        return document

    def _section(self, document: PolicyDocument, section_id: str) -> SectionText:
        for part in document.sections:
            if part.id == section_id:
                content = cap_text(part.content, self._limits.policy.section_chars)
                return SectionText(
                    id=part.id, title=part.title, content=content.text, truncated=content.truncated
                )
        raise NotFoundError(
            f"{document.name} has no such section.",
            sections=[part.id for part in document.sections],
        )


def _known(name: str) -> PolicyName:
    for known in POLICY_NAMES:
        if name == known:
            return known
    raise ValidationFailedError(
        "name is not one of the policy documents.", names=list(POLICY_NAMES)
    )


def _view(
    document: PolicyDocument,
    *,
    sections: tuple[SectionOutline, ...] | None,
    section: SectionText | None,
) -> PolicyView:
    return PolicyView(
        name=_known(document.name),
        version=document.version,
        effective_date=document.effective_date,
        sections=sections,
        section=section,
    )
