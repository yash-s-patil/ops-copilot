from typing import Annotated

from fastmcp import Context, FastMCP
from pydantic import Field

from opscp_common.limits import Limits
from opscp_mcp.authz.policy import Permission
from opscp_mcp.services.policies import PolicyName, PolicyView
from opscp_mcp.services.runbooks import RunbookSearchResult
from opscp_mcp.tools.models import ToolOutput
from opscp_mcp.tools.names import GET_POLICY_DOCUMENT, SEARCH_RUNBOOKS
from opscp_mcp.tools.runner import answer
from opscp_mcp.tools.shared import READ_ONLY, UNTRUSTED_TEXT

REQUIRES = {
    SEARCH_RUNBOOKS: Permission.RUNBOOKS_READ,
    GET_POLICY_DOCUMENT: Permission.POLICIES_READ,
}


def register(server: FastMCP, limits: Limits) -> None:
    @server.tool(
        name=SEARCH_RUNBOOKS,
        title="Search the runbooks",
        annotations=READ_ONLY,
        description=(
            "Search the support runbooks for a procedure. Describe the cause in a few words, "
            "for example 'address validation failed, order stuck in packed'. Returns the "
            "best-matching section of each matching runbook (default "
            f"{limits.runbooks.top_k_default}, at most {limits.runbooks.top_k_max}). A result "
            f"marked quarantined was withheld. {UNTRUSTED_TEXT}"
        ),
    )
    async def search_runbooks(
        ctx: Context,
        query: Annotated[
            str,
            Field(
                description=(
                    f"{limits.search.query_chars_min} to {limits.search.query_chars_max} "
                    "characters describing the problem."
                )
            ),
        ],
        top_k: Annotated[int | None, Field(description="How many runbooks to return.")] = None,
    ) -> ToolOutput[RunbookSearchResult]:
        return await answer(
            ctx,
            SEARCH_RUNBOOKS,
            limits.tool_timeout.search_runbooks_s,
            lambda services, identity: services.runbooks.search(identity, query, top_k=top_k),
        )

    @server.tool(
        name=GET_POLICY_DOCUMENT,
        title="Read a policy",
        annotations=READ_ONLY,
        description=(
            "Read a policy document. Without a section it returns the list of sections with a "
            "one-line summary of each. With a section id it returns that section. The whole "
            f"document is never returned in one call. {UNTRUSTED_TEXT}"
        ),
    )
    async def get_policy_document(
        ctx: Context,
        name: Annotated[PolicyName, Field(description="Which policy document.")],
        section: Annotated[
            str | None, Field(description="A section id from the document's list of sections.")
        ] = None,
    ) -> ToolOutput[PolicyView]:
        return await answer(
            ctx,
            GET_POLICY_DOCUMENT,
            limits.tool_timeout.get_policy_document_s,
            lambda services, identity: services.policies.get(identity, name, section=section),
        )
