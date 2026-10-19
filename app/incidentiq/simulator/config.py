from typing import Literal

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SimulatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIMULATOR_", env_file=".env", extra="ignore")

    role: Literal["checkout", "inventory"] = "checkout"
    inventory_url: AnyHttpUrl = AnyHttpUrl("http://inventory:8000")
    dependency_timeout_seconds: float = Field(default=3.0, ge=0.1, le=10)
    otlp_endpoint: AnyHttpUrl | None = None
