from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    """Fixed fields, never changed after it is built."""

    model_config = ConfigDict(extra="forbid", frozen=True)
