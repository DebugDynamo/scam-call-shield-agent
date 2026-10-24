"""Validated runtime configuration (pydantic-settings). All secrets come from the environment only."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLM_PROVIDERS = ("gemini", "mock")
STT_PROVIDERS = ("google", "sarvam", "mock")
TTS_PROVIDERS = ("google", "elevenlabs", "sarvam", "mock")

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, domain-neutral voice assistant. Keep replies short and conversational because "
    "they may be spoken aloud: no markdown, no lists, no special characters. Use the provided tools for "
    "arithmetic, searching, and managing tasks instead of guessing. For consequential actions (deleting "
    "things) the tool will require a human approval: tell the user an approval is pending and never claim "
    "the action happened until the tool result says so. If a tool reports an error or that it is "
    "unavailable, say so plainly."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)

    # server
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = Field(8003, ge=1, le=65535)
    service_name: str = "repo-voice-ai"
    version: str = "0.1.0"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    internal_api_key: str | None = None
    log_level: str = "INFO"

    # data
    data_mode: Literal["mock", "postgres"] = "mock"
    database_url: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432
    db_username: str = "postgres"
    db_password: str = "postgres"
    db_database: str = "garbathon_voice"
    db_ssl: bool = False
    persist_transcripts: bool = True
    session_ttl_seconds: int = Field(1800, ge=5)
    cleanup_interval_seconds: int = Field(60, ge=1)

    # providers
    llm_provider: str = "gemini"
    stt_provider: str = "sarvam"
    tts_provider: str = "google"
    gemini_api_key: str | None = Field(default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    gemini_model: str = "gemini-3.8-flash"
    gemini_tts_voice: str = "Kore"
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    sarvam_api_key: str | None = None
    sarvam_tts_voice: str = "shubh"
    google_application_credentials: str | None = None
    google_credentials_json: str | None = None
    voice_language: str = "en-IN"
    llm_timeout_seconds: float = Field(30.0, gt=0)
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_tool_steps: int = Field(5, ge=1, le=20)

    # tools
    tool_permissions: str = "tasks:read,tasks:write,calc:use,search:read,approvals:request,agent:invoke,scam:analyze"
    approval_ttl_seconds: int = Field(300, ge=5)
    search_api_url: str | None = None
    search_api_key: str | None = None
    agent_api_url: str | None = None
    agent_api_key: str | None = None
    tool_http_timeout_seconds: float = Field(10.0, gt=0)

    # webrtc
    ice_servers: str = "stun:stun.l.google.com:19302"

    # telephony (Plivo)
    plivo_auth_id: str | None = None
    plivo_auth_token: str | None = None
    plivo_phone_number: str | None = None
    public_base_url: str | None = None
    plivo_verify_signature: bool = True
    plivo_allow_inbound: bool = False

    @field_validator("llm_provider")
    @classmethod
    def _llm(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in LLM_PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {LLM_PROVIDERS}, got {v!r}")
        return v

    @field_validator("stt_provider")
    @classmethod
    def _stt(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in STT_PROVIDERS:
            raise ValueError(f"STT_PROVIDER must be one of {STT_PROVIDERS}, got {v!r}")
        return v

    @field_validator("tts_provider")
    @classmethod
    def _tts(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in TTS_PROVIDERS:
            raise ValueError(f"TTS_PROVIDER must be one of {TTS_PROVIDERS}, got {v!r}")
        return v

    @field_validator("public_base_url")
    @classmethod
    def _public(cls, v: str | None) -> str | None:
        if v:
            v = v.strip().rstrip("/")
            if not v.startswith(("http://", "https://")):
                raise ValueError("PUBLIC_BASE_URL must start with http:// or https://")
        return v or None

    @model_validator(mode="after")
    def _blank_to_none(self) -> Settings:
        for name in (
            "gemini_api_key", "elevenlabs_api_key", "sarvam_api_key", "internal_api_key", "database_url",
            "plivo_auth_id", "plivo_auth_token", "plivo_phone_number", "agent_api_url", "agent_api_key",
            "search_api_url", "search_api_key", "google_application_credentials", "google_credentials_json",
        ):
            val = getattr(self, name)
            if isinstance(val, str) and not val.strip():
                setattr(self, name, None)
        return self

    # ---- derived ----
    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if "*" in origins:
            raise ValueError("CORS_ORIGINS must not contain '*'")
        return origins

    @property
    def granted_permissions(self) -> set[str]:
        return {p.strip() for p in self.tool_permissions.split(",") if p.strip()}

    @property
    def dsn(self) -> str:
        """DATABASE_URL takes precedence over DB_* parts."""
        if self.database_url:
            return self.database_url
        ssl = "require" if self.db_ssl else "disable"
        return (
            f"postgresql://{quote(self.db_username, safe='')}:{quote(self.db_password, safe='')}"
            f"@{self.db_host}:{self.db_port}/{self.db_database}?sslmode={ssl}"
        )

    @property
    def ice_server_list(self) -> list[str]:
        return [s.strip() for s in self.ice_servers.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
