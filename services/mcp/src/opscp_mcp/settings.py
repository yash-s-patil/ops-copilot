"""Configuration of the MCP server, read once at start-up."""

from pydantic import DirectoryPath, FilePath
from pydantic_settings import BaseSettings, SettingsConfigDict

from opscp_common.logging import LogLevel
from opscp_common.settings import LOCAL, Environment

SERVICE_NAME = "mcp"


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    environment: Environment
    log_level: LogLevel = "INFO"
    limits_file: FilePath
    runbooks_dir: DirectoryPath

    @property
    def json_logs(self) -> bool:
        return self.environment != LOCAL
