"""Settings. Every secret comes from environment variables. Nothing is stored on disk."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "sqlite:///./aip.db"
    admin_token: SecretStr = SecretStr("")

    # Chat provider: fake | openai_compat | anthropic
    llm_provider: str = "fake"
    # Embedding provider: fake | openai_compat (Anthropic has no embeddings API)
    embedding_provider: str = "fake"
    openai_base_url: str = "http://localhost:11434/v1"  # Ollama default
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "llama3.1"
    openai_embed_model: str = "nomic-embed-text"
    anthropic_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = 20
    llm_price_per_mtok_json: str = ""  # {"model": {"input": usd, "output": usd}} per million tokens
    llm_fallback_provider: str = ""  # empty: no failover. Else one of fake | openai_compat | anthropic
    anthropic_model: str = "claude-sonnet-5-5"

    prompts_dir: Path = Path("prompts")
    prompt_version: str = "triage_v1"
    max_agent_steps: int = 5
    rate_limit_per_minute: int = 60
    checkpoint_url: str = "sqlite:///./aip-checkpoints.sqlite"
    approval_limit_minor: int = 10000
    authority_path: str = "docs/engagement/AUTHORITY.md"
    action_port: Literal["mcp", "inprocess"] = "inprocess"
    idempotency_ttl_hours: int = 72
    blob_backend: Literal["local", "s3"] = "local"
    queue_backend: Literal["memory", "sqs"] = "memory"
    secret_backend: Literal["env", "aws"] = "env"
    blob_root: str = "./aip-blobs"
    s3_bucket: str = ""
    sqs_queue_url: str = ""
    sqs_dlq_url: str = ""
    secret_prefix: str = ""  # prepended to a secret name when the backend is aws
    aws_region: str = "us-east-1"
    aws_endpoint_url: str = ""  # set only for an emulator such as moto
    case_max_steps: int = 6
    case_max_tokens: int = 4000

    # Mock CRM connector (OAuth2 client credentials)
    crm_base_url: str = "http://localhost:9001"
    crm_client_id: str = ""
    crm_client_secret: SecretStr = SecretStr("")

    # Outbound webhooks: block loopback/private targets unless this is true (demo only)
    allow_private_webhook_targets: bool = False
