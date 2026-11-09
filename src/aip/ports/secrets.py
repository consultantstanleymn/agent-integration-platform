"""Secret provider port and an environment adapter."""

import os
from typing import Protocol


class SecretProvider(Protocol):
    def get(self, name: str) -> str: ...


class EnvSecretProvider:
    """Reads the upper-case environment variable with the same name."""

    def get(self, name: str) -> str:
        key = name.upper().replace("-", "_").replace("/", "_")
        try:
            return os.environ[key]
        except KeyError:
            raise KeyError(f"secret not set: {key}") from None
