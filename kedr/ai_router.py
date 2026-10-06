#!/usr/bin/env python3
"""KEDR AI failover router.

This module is intentionally independent from MAX transport/webhook code.
It only chooses an AI provider and returns text. Integration into the
current Yandex Cloud function should replace only the existing AI call.

No secrets are stored in this file. Providers are enabled only when both:
  1) the provider is listed in KEDR_AI_ORDER; and
  2) the required environment variable/secret exists.

Default order preserves the historical GigaChat route first, then adds
independent fallbacks.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence


DEFAULT_ORDER = ("gigachat", "groq", "openrouter", "gemini")
COOLDOWN_SECONDS = int(os.getenv("KEDR_AI_COOLDOWN_SECONDS", "1800"))
REQUEST_TIMEOUT = float(os.getenv("KEDR_AI_TIMEOUT_SECONDS", "45"))

# Process-local cooldown. In Yandex Cloud this is best-effort per warm instance.
_COOLDOWN_UNTIL: dict[str, float] = {}


@dataclass(frozen=True)
class AIResult:
    text: str
    provider: str
    model: str


class AIProviderError(RuntimeError):
    def __init__(self, provider: str, message: str, *, retryable: bool = True, status: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status = status


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _json_request(
    url: str,
    *,
    method: str = "POST",
    headers: Mapping[str, str] | None = None,
    payload: object | None = None,
    timeout: float = REQUEST_TIMEOUT,
) -> tuple[int, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if payload is not None and not any(k.lower() == "content-type" for k in (headers or {})):
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"raw": body[:1000]}
        return int(exc.code), parsed
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"network_error: {exc}") from exc


def _messages_with_system(system_prompt: str, messages: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if system_prompt.strip():
        out.append({"role": "system", "content": system_prompt.strip()})
    for item in messages:
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant", "system"} and content:
            out.append({"role": role, "content": content})
    return out


def _extract_openai_text(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    msg = first.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    return content.strip() if isinstance(content, str) else ""


def _provider_error(provider: str, status: int, data: object) -> AIProviderError:
    # Quota/rate-limit/upstream problems should immediately fall through.
    retryable = status in {402, 403, 408, 409, 425, 429} or status >= 500
    compact = json.dumps(data, ensure_ascii=False)[:700] if data is not None else ""
    return AIProviderError(
        provider,
        f"{provider} HTTP {status}: {compact}",
        retryable=retryable,
        status=status,
    )


def _openai_compatible(
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    messages: Sequence[Mapping[str, str]],
    extra_headers: Mapping[str, str] | None = None,
) -> AIResult:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    headers.update(extra_headers or {})
    status, data = _json_request(
        base_url.rstrip("/") + "/chat/completions",
        headers=headers,
        payload={
            "model": model,
            "messages": _messages_with_system(system_prompt, messages),
            "temperature": 0.25,
            "stream": False,
        },
    )
    if status < 200 or status >= 300:
        raise _provider_error(provider, status, data)
    text = _extract_openai_text(data)
    if not text:
        raise AIProviderError(provider, f"{provider}: empty response", retryable=True, status=status)
    return AIResult(text=text, provider=provider, model=model)


def _call_groq(system_prompt: str, messages: Sequence[Mapping[str, str]]) -> AIResult:
    key = _env("GROQ_API_KEY")
    if not key:
        raise AIProviderError("groq", "GROQ_API_KEY is not configured", retryable=False)
    model = _env("KEDR_GROQ_MODEL") or "llama-3.3-70b-versatile"
    return _openai_compatible(
        "groq",
        "https://api.groq.com/openai/v1",
        key,
        model,
        system_prompt,
        messages,
    )


def _call_openrouter(system_prompt: str, messages: Sequence[Mapping[str, str]]) -> AIResult:
    key = _env("OPENROUTER_API_KEY")
    if not key:
        raise AIProviderError("openrouter", "OPENROUTER_API_KEY is not configured", retryable=False)
    # Explicitly use OpenRouter's free router unless overridden.
    model = _env("KEDR_OPENROUTER_MODEL") or "openrouter/free"
    headers = {}
    if _env("KEDR_HTTP_REFERER"):
        headers["HTTP-Referer"] = _env("KEDR_HTTP_REFERER")
    headers["X-Title"] = _env("KEDR_APP_TITLE") or "KEDR"
    return _openai_compatible(
        "openrouter",
        "https://openrouter.ai/api/v1",
        key,
        model,
        system_prompt,
        messages,
        extra_headers=headers,
    )


def _call_gemini(system_prompt: str, messages: Sequence[Mapping[str, str]]) -> AIResult:
    key = _env("GEMINI_API_KEY")
    if not key:
        raise AIProviderError("gemini", "GEMINI_API_KEY is not configured", retryable=False)
    model = _env("KEDR_GEMINI_MODEL") or "gemini-2.5-flash"

    contents = []
    for item in messages:
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        contents.append(
            {
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": content}],
            }
        )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + urllib.parse.quote(model, safe="-._")
        + ":generateContent?key="
        + urllib.parse.quote(key, safe="")
    )
    payload: dict[str, object] = {
        "contents": contents,
        "generationConfig": {"temperature": 0.25},
    }
    if system_prompt.strip():
        payload["systemInstruction"] = {"parts": [{"text": system_prompt.strip()}]}

    status, data = _json_request(url, headers={"Content-Type": "application/json"}, payload=payload)
    if status < 200 or status >= 300:
        raise _provider_error("gemini", status, data)

    text = ""
    if isinstance(data, dict):
        candidates = data.get("candidates")
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                content = first.get("content")
                if isinstance(content, dict):
                    parts = content.get("parts")
                    if isinstance(parts, list):
                        text = "\n".join(
                            p.get("text", "").strip()
                            for p in parts
                            if isinstance(p, dict) and isinstance(p.get("text"), str) and p.get("text", "").strip()
                        ).strip()
    if not text:
        raise AIProviderError("gemini", "gemini: empty response", retryable=True, status=status)
    return AIResult(text=text, provider="gemini", model=model)


_GIGA_TOKEN = ""
_GIGA_TOKEN_EXPIRES_AT = 0.0


def _giga_access_token(credentials: str) -> str:
    global _GIGA_TOKEN, _GIGA_TOKEN_EXPIRES_AT
    now = time.time()
    if _GIGA_TOKEN and now < _GIGA_TOKEN_EXPIRES_AT - 60:
        return _GIGA_TOKEN

    # Historical KEDR/MAX setup used GigaChat Basic credentials.
    auth = credentials
    if ":" in credentials and not credentials.startswith("Basic "):
        auth = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    if auth.startswith("Basic "):
        auth = auth[6:].strip()

    data = urllib.parse.urlencode({"scope": _env("GIGACHAT_SCOPE") or "GIGACHAT_API_PERS"}).encode("utf-8")
    req = urllib.request.Request(
        "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
            token = str(payload.get("access_token", "")).strip()
            expires_at_raw = payload.get("expires_at")
            if not token:
                raise AIProviderError("gigachat", "GigaChat OAuth returned no token", retryable=True)
            _GIGA_TOKEN = token
            try:
                # GigaChat may return milliseconds.
                expires = float(expires_at_raw or 0)
                if expires > 10_000_000_000:
                    expires /= 1000.0
                _GIGA_TOKEN_EXPIRES_AT = expires if expires > now else now + 1500
            except Exception:
                _GIGA_TOKEN_EXPIRES_AT = now + 1500
            return token
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise _provider_error("gigachat", int(exc.code), {"raw": body[:700]}) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AIProviderError("gigachat", f"GigaChat OAuth network error: {exc}", retryable=True) from exc


def _call_gigachat(system_prompt: str, messages: Sequence[Mapping[str, str]]) -> AIResult:
    credentials = _env("GIGACHAT_CREDENTIALS") or _env("GIGA_KEY")
    if not credentials:
        raise AIProviderError("gigachat", "GIGACHAT_CREDENTIALS/GIGA_KEY is not configured", retryable=False)
    token = _giga_access_token(credentials)
    model = _env("KEDR_GIGACHAT_MODEL") or "GigaChat-2-Pro"
    status, data = _json_request(
        "https://api.giga.chat/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        payload={
            "model": model,
            "messages": _messages_with_system(system_prompt, messages),
            "temperature": 0.25,
            "stream": False,
        },
    )
    if status < 200 or status >= 300:
        raise _provider_error("gigachat", status, data)
    text = _extract_openai_text(data)
    if not text:
        raise AIProviderError("gigachat", "GigaChat returned empty response", retryable=True, status=status)
    return AIResult(text=text, provider="gigachat", model=model)


PROVIDERS: dict[str, Callable[[str, Sequence[Mapping[str, str]]], AIResult]] = {
    "gigachat": _call_gigachat,
    "groq": _call_groq,
    "openrouter": _call_openrouter,
    "gemini": _call_gemini,
}


def configured_providers() -> list[str]:
    checks = {
        "gigachat": bool(_env("GIGACHAT_CREDENTIALS") or _env("GIGA_KEY")),
        "groq": bool(_env("GROQ_API_KEY")),
        "openrouter": bool(_env("OPENROUTER_API_KEY")),
        "gemini": bool(_env("GEMINI_API_KEY")),
    }
    return [p for p in provider_order() if checks.get(p, False)]


def provider_order() -> list[str]:
    raw = _env("KEDR_AI_ORDER")
    order = [x.strip().lower() for x in raw.split(",")] if raw else list(DEFAULT_ORDER)
    clean: list[str] = []
    for name in order:
        if name in PROVIDERS and name not in clean:
            clean.append(name)
    return clean


def provider_status() -> dict[str, object]:
    now = time.monotonic()
    configured = set(configured_providers())
    return {
        "order": provider_order(),
        "configured": sorted(configured),
        "cooldown": {
            name: max(0, int(until - now))
            for name, until in _COOLDOWN_UNTIL.items()
            if until > now
        },
    }


def answer(
    messages: Sequence[Mapping[str, str]],
    *,
    system_prompt: str = "",
) -> AIResult:
    """Return the first successful provider response.

    429/403 quota errors, timeouts and 5xx errors put that provider on a
    temporary cooldown and immediately try the next configured provider.
    Invalid/unconfigured providers are simply skipped. No retry loop spins
    against the same provider.
    """
    now = time.monotonic()
    configured = set(configured_providers())
    errors: list[str] = []

    for name in provider_order():
        if name not in configured:
            continue
        until = _COOLDOWN_UNTIL.get(name, 0.0)
        if until > now:
            continue

        call = PROVIDERS[name]
        try:
            return call(system_prompt, messages)
        except AIProviderError as exc:
            errors.append(f"{name}: {exc}")
            if exc.retryable:
                _COOLDOWN_UNTIL[name] = time.monotonic() + COOLDOWN_SECONDS
            continue
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            _COOLDOWN_UNTIL[name] = time.monotonic() + COOLDOWN_SECONDS
            continue

    if not configured:
        raise AIProviderError(
            "router",
            "No KEDR AI providers are configured",
            retryable=False,
        )
    raise AIProviderError(
        "router",
        "All configured KEDR AI providers are unavailable: " + " | ".join(errors[-4:]),
        retryable=True,
    )
