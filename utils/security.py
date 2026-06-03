"""Security utilities for prompt sanitization and input validation."""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ── Prompt injection pattern catalogue ───────────────────────────────────────
# Pattern 1: classic override phrasing
_INJECTION_OVERRIDE = re.compile(
    r'(?i)(ignore|forget|override|disregard|bypass|skip|stop following)\s+'
    r'(previous|above|prior|all|system|the|your)\s+'
    r'(instructions?|rules?|prompts?|context|constraints?|guidelines?)',
    re.MULTILINE,
)

# Pattern 2: role-prefix injection  (e.g. "SYSTEM:", "### System", "[INST]", "<|system|>")
_INJECTION_ROLE_PREFIX = re.compile(
    r'(?i)(^|\n)\s*'
    r'(###\s*)?(system|assistant|user|human|inst|instruction|prompt)\s*[:>\]|]',
    re.MULTILINE,
)

# Pattern 3: model-specific special tokens  (<|...|>, [INST], <<SYS>>, etc.)
_INJECTION_SPECIAL_TOKENS = re.compile(
    r'<\|[^|]{0,30}\|>|'          # <|im_start|>, <|system|>, etc.
    r'\[/?INST\]|'                  # Llama 2 / Mistral instruction tokens
    r'<</?SYS>>|'                   # Llama 2 system tags
    r'\[/?s\]',                     # Some tokenizer special tokens
    re.IGNORECASE,
)

# Pattern 4: control characters (excluding tab \x09 and newline \x0a, \x0d)
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _normalize_unicode(text: str) -> str:
    """
    NFKC-normalize unicode so homoglyph attacks (e.g. using Cyrillic 'о'
    instead of Latin 'o') are collapsed to their canonical ASCII equivalents
    before pattern matching.
    """
    return unicodedata.normalize("NFKC", text)


def sanitize_for_prompt(text: Optional[str], max_len: int = 300) -> str:
    """
    Sanitize external text before injecting into LLM prompts.

    Defence layers (applied in order):
    1. Strip dangerous C0/C1 control characters
    2. NFKC unicode normalization (homoglyph collapse)
    3. Remove model special tokens (<|...|>, [INST], <<SYS>>)
    4. Remove role-prefix injection patterns (SYSTEM:, ### User, etc.)
    5. Remove classic override-instruction patterns
    6. Truncate to max_len

    Returns empty string for None or empty input.
    """
    if not text:
        return ""

    # Layer 1 — control chars
    text = _CONTROL_CHARS.sub('', text)

    # Layer 2 — unicode normalization
    text = _normalize_unicode(text)

    # Layer 3 — model special tokens
    text = _INJECTION_SPECIAL_TOKENS.sub('[FILTERED]', text)

    # Layer 4 — role prefix injection
    text = _INJECTION_ROLE_PREFIX.sub(r'\1[FILTERED]', text)

    # Layer 5 — classic override patterns
    text = _INJECTION_OVERRIDE.sub('[FILTERED]', text)

    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len] + '...'
    return text


def sanitize_news_item(title: str, description: str = "") -> tuple[str, str]:
    """Sanitize news title and description for safe prompt injection."""
    return (
        sanitize_for_prompt(title, max_len=200),
        sanitize_for_prompt(description, max_len=400),
    )


def sanitize_lesson(lesson: str) -> str:
    """Sanitize critic lesson text before injecting into orchestrator prompt."""
    return sanitize_for_prompt(lesson, max_len=300)
