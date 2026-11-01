"""Evidence: redaction, size caps and the record built from a capability's result (design §7, §14.3).

The redactor runs on every result before storage and before any model message. It removes what looks
like a credential wherever it appears: values of environment variables with telling names, tokens, keys,
private keys, basic-auth URLs and Authorization headers. Redactions are counted on the evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, cast

from k8sholmes.records import Call, Coverage, Evidence

REDACTED = "<redacted>"
SENSITIVE_NAME = re.compile(r"(?i)(key|token|password|passwd|secret|credential|auth)")  # env var names
# dict keys: structural keys such as `key`, `secretName` or `secretKeyRef` name things and are kept
SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|token|secret|credential|api[_-]?key|access[_-]?key|private[_-]?key)"
)
# patterns matched inside free text and string values
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "key_like",
        re.compile(
            r"\b(?:sk|tok|key|pat|api|secret|token)[_-](?:live|test|prod|[a-z]{2,8})?[_-]?[A-Za-z0-9]{16,}\b"
        ),
    ),
    ("basic_auth_url", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    (
        "authorization_header",
        re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer|basic)?\s*[A-Za-z0-9._~+/=-]{8,}"),
    ),
    (
        "assignment",
        re.compile(
            r"(?i)\b([a-z0-9_.-]*(?:api[_-]?key|token|password|passwd|secret|credential)[a-z0-9_.-]*)(\s*[:=]\s*)([\"']?)([^\s\"',;&]{4,})"
        ),
    ),
]


def redact_text(text: str) -> tuple[str, int]:
    """Replace credentials in free text; returns the text and how many replacements were made."""
    count = 0
    for name, pattern in PATTERNS:
        if name == "authorization_header":
            text, n = pattern.subn(lambda m: f"{m.group(1)}{REDACTED}", text)
        elif name == "assignment":
            text, n = pattern.subn(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}", text)
        else:
            text, n = pattern.subn(REDACTED, text)
        count += n
    return text, count


def redact(data: Any) -> tuple[Any, int]:
    """Redact a JSON-like value: dicts, lists and strings; env-style `{"name": …, "value": …}` pairs too."""
    if isinstance(data, str):
        return redact_text(data)
    if isinstance(data, list):
        return _redact_list(cast(list[Any], data))
    if isinstance(data, dict):
        return _redact_mapping(cast(dict[str, Any], data))
    return data, 0


def _redact_list(items: list[Any]) -> tuple[list[Any], int]:
    """Items one by one; in an argument list, the value after a flag with a telling name (`--token X`)."""
    out: list[Any] = []
    count = 0
    redact_next = False
    for item in items:
        if redact_next and isinstance(item, str) and not item.startswith("-"):
            out.append(REDACTED)
            count += 1
            redact_next = False
            continue
        redact_next = (
            isinstance(item, str)
            and item.startswith("-")
            and "=" not in item
            and bool(SENSITIVE_NAME.search(item))
        )
        cleaned, n = redact(item)
        out.append(cleaned)
        count += n
    return out, count


def _redact_mapping(mapping: dict[str, Any]) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    count = 0
    name = mapping.get("name")
    env_name_is_sensitive = isinstance(name, str) and bool(SENSITIVE_NAME.search(name))
    for key, value in mapping.items():
        key_s = str(key)
        env_value = key_s == "value" and env_name_is_sensitive
        named_secret = (
            isinstance(value, str)
            and bool(SENSITIVE_KEY.search(key_s))
            and not key_s.lower().endswith(("name", "ref", "path", "file"))
        )
        if (env_value or named_secret) and value not in (None, ""):
            result[key_s] = REDACTED
            count += 1
        else:
            cleaned, n = redact(value)
            result[key_s] = cleaned
            count += n
    return result, count


def digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def cap(data: Any, *, max_bytes: int = 262_144, max_lines: int = 2000) -> tuple[Any, bool]:
    """Cut a result to size: logs by lines and bytes, lists by items, anything else by its JSON size."""
    if isinstance(data, str):
        lines = data.splitlines(keepends=True)
        truncated = len(lines) > max_lines
        text = "".join(lines[-max_lines:]) if truncated else data
        if len(text.encode()) > max_bytes:
            text = text.encode()[-max_bytes:].decode(errors="ignore")
            truncated = True
        return text, truncated
    if isinstance(data, list):
        items: list[Any] = list(cast(list[Any], data))
        truncated = False
        while items and len(json.dumps(items, default=str).encode()) > max_bytes:
            items = items[: max(1, len(items) // 2)]
            truncated = True
        return items, truncated
    if len(json.dumps(data, default=str).encode()) > max_bytes:
        note = f"the result was larger than {max_bytes} bytes and is kept only as its hash"
        return {"truncated": True, "note": note}, True
    return data, False


@dataclass(frozen=True)
class Result:
    """What a capability returns (design §5.1)."""

    summary: str
    data: Any
    coverage: Coverage
    coverage_reason: str = ""
    error: str | None = None


def evidence_from_result(
    result: Result,
    call: Call,
    *,
    query: dict[str, Any],
    environment_id: str,
    systems: list[str],
    max_bytes: int = 262_144,
) -> Evidence:
    """Hash the full result, redact everything that leaves (data, summary, query, reason, error), cap it."""
    full_hash = digest(result.data)
    cleaned, redactions = redact(result.data)
    capped, truncated = cap(cleaned, max_bytes=max_bytes)
    summary, n_summary = redact_text(result.summary)
    reason, n_reason = redact_text(result.coverage_reason)
    error, n_error = redact_text(result.error) if result.error else (None, 0)
    return Evidence(
        investigation_id=call.investigation_id or None,
        environment_id=environment_id,
        step_id=call.step_id or None,
        call_id=call.id,
        source=call.capability_id,
        systems=systems,
        query=redact(query)[0],
        data=capped,
        sha256=full_hash,
        truncated=truncated,
        coverage=result.coverage,
        coverage_reason=reason,
        redactions=redactions + n_summary + n_reason + n_error,
        error=error,
        summary=summary,
    )
