"""The meridian tables and columns that the server reads."""

from sqlalchemy import BigInteger, Column, DateTime, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import JSONB

_core = MetaData(schema="core")

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
    Column("customer_id", BigInteger),
    Column("status", Text),
)

refunds = Table(
    "refunds",
    _core,
    Column("id", BigInteger, primary_key=True),
    Column("order_id", BigInteger),
    Column("amount_minor", BigInteger),
    Column("status", Text),
)
