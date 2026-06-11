from enum import Enum
from typing import Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class EnvironmentOption(str, Enum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables or a .env file.
    """
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: EnvironmentOption = EnvironmentOption.LOCAL
    LOG_LEVEL: str = "INFO"

    APP_NAME: str = "AI CBP MDO Service"
    APP_DESC: str = "API for managing CBP MDO approval requests"
    APP_VERSION: str = "1.0.0"
    APP_ROOT_PATH: str = ""

    REQUIRED_ROLES: list[str] = ["MDO_ADMIN", "MDO_LEADER"]

    KB_BASE_URL: str = "https://portal.dev.karmayogibharat.net"
    KB_AUTH_TOKEN: str = ""
    
    SUNBIRD_SSO_REALM: str = "sunbird"
    SUNBIRD_SSO_URL: str = ""

    DATABASE_URL: str
    
    DEFAULT_RELEVANCY_SCORE: int = 90

    NOTIFICATION_BASE_URL: str = ""
    ENABLE_EMAIL_NOTIFICATION: bool = False

# Create a settings instance that can be imported by other modules
settings = Settings()