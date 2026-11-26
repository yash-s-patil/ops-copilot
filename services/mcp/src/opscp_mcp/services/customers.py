from datetime import datetime

from opscp_common.errors import NotFoundError, ValidationFailedError
from opscp_common.vocabulary import Region, Tier
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Permission
from opscp_mcp.ports.meridian import CustomerRecord, Customers
from opscp_mcp.services.caps import Result
from opscp_mcp.services.inputs import require_email, require_id
from opscp_mcp.services.masking import mask_email, mask_phone
from opscp_mcp.services.scope import scope_of


class CustomerView(Result):
    customer_id: int
    display_name: str
    tier: Tier
    region: Region
    email: str
    phone: str | None
    contact_masked: bool
    open_ticket_count: int
    created_at: datetime


class CustomerService:
    def __init__(self, customers: Customers) -> None:
        self._customers = customers

    async def lookup(
        self, identity: Identity, *, customer_id: int | None = None, email: str | None = None
    ) -> CustomerView:
        identity.require(Permission.CUSTOMERS_READ)
        scope = scope_of(identity)
        if customer_id is not None and email is None:
            record = await self._customers.by_id(scope, require_id(customer_id, "customer_id"))
        elif email is not None and customer_id is None:
            record = await self._customers.by_email(scope, require_email(email))
        else:
            raise ValidationFailedError("Give exactly one of customer_id and email.")
        if record is None:
            raise NotFoundError("No customer with that id or email is visible to you.")
        return _view(record, masked=not identity.can(Permission.PII_READ))


def _view(record: CustomerRecord, *, masked: bool) -> CustomerView:
    phone = record.phone
    return CustomerView(
        customer_id=record.id,
        display_name=record.full_name,
        tier=record.tier,
        region=record.region,
        email=mask_email(record.email) if masked else record.email,
        phone=mask_phone(phone) if masked and phone is not None else phone,
        contact_masked=masked,
        open_ticket_count=record.open_ticket_count,
        created_at=record.created_at,
    )
