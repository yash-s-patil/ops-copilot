from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    """What a backend hands to a service: fixed fields, never changed after it is built."""

    model_config = ConfigDict(extra="forbid", frozen=True)
