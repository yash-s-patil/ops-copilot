"""The meridian tables and columns that the server reads."""

from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, Table, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB

_core = MetaData(schema="core")
_control = MetaData(schema="control")

customers = Table(
    "customers",
    _core,
    Column("id", BigInteger, primary_key=True),
    Column("full_name", Text),
    Column("email", Text),
    Column("phone", Text),
    Column("tier", Text),
    Column("region", Text),
    Column("created_at", DateTime(timezone=True)),
)

orders = Table(
    "orders",
    _core,
    Column("id", BigInteger, primary_key=True),
    Column("customer_id", BigInteger),
    Column("status", Text),
    Column("amount_minor", BigInteger),
    Column("currency", Text),
    Column("region", Text),
    Column("carrier", Text),
    Column("tracking_ref", Text),
    Column("placed_at", DateTime(timezone=True)),
    Column("status_changed_at", DateTime(timezone=True)),
)

order_events = Table(
    "order_events",
    _core,
    Column("id", BigInteger, primary_key=True),
    Column("order_id", BigInteger),
    Column("event_type", Text),
    Column("payload", JSONB),
    Column("occurred_at", DateTime(timezone=True)),
)

tickets = Table(
    "tickets",
    _core,
    Column("id", BigInteger, primary_key=True),
    Column("order_id", BigInteger),
    Column("customer_id", BigInteger),
    Column("region", Text),
    Column("status", Text),
    Column("priority", Text),
    Column("queue", Text),
    Column("escalated_by_oid", Uuid),
    Column("escalated_by_name", Text),
    Column("escalated_at", DateTime(timezone=True)),
    Column("recommendation", JSONB),
    Column("summary", Text),
    Column("version", Integer),
    Column("updated_at", DateTime(timezone=True)),
)

ticket_notes = Table(
    "ticket_notes",
    _core,
    Column("id", BigInteger, primary_key=True),
    Column("ticket_id", BigInteger),
    Column("author_kind", Text),
    Column("author_name", Text),
    Column("body", Text),
    Column("created_at", DateTime(timezone=True)),
)

refunds = Table(
    "refunds",
    _core,
    Column("id", BigInteger, primary_key=True),
    Column("order_id", BigInteger),
    Column("ticket_id", BigInteger),
    Column("amount_minor", BigInteger),
    Column("currency", Text),
    Column("reason_code", Text),
    Column("status", Text),
    Column("failure_reason", Text),
    Column("requested_by_name", Text),
    Column("approved_by_name", Text),
    Column("created_at", DateTime(timezone=True)),
)

pending_actions = Table(
    "pending_actions",
    _control,
    Column("id", Uuid, primary_key=True),
    Column("tool", Text),
    Column("args", JSONB),
    Column("requester_oid", Uuid),
    Column("requester_name", Text),
    Column("approval_kind", Text),
    Column("status", Text),
    Column("approver_name", Text),
    Column("ticket_id", BigInteger),
    Column("expires_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
)
