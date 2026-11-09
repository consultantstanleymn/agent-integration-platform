"""One redaction interface for logs, audit rows and case records."""

from typing import Any, Protocol

from aip.observability import redact


class Redactor(Protocol):
    def redact(self, value: Any) -> Any: ...


class RegexRedactor:
    """Masks emails, phones, SSNs, card numbers and secrets with the existing regex rules."""

    def redact(self, value: Any) -> Any:
        return redact(value)


def default_redactor() -> Redactor:
    return RegexRedactor()
