"""Seed job configuration, read once from the environment."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

from opscp_seed.model import Engineer


class DemoUsers(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    l1_west: Engineer
    l1_south: Engineer
    l2_west: Engineer


class SeedSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEED_", env_nested_delimiter="__", extra="ignore", frozen=True
    )

    target_host: str
    limits_file: Path
    demo: DemoUsers
