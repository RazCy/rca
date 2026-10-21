"""
Rule / template based Natural Language → DQL converter.
No external LLM, no Davis CoPilot scope required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class NlpResult:
    dql: str
    intent: str
    confidence: float
    explanation: str
    filters: dict = field(default_factory=dict)
    time_range: Optional[str] = None
    fallback: bool = False


# ---------------------------------------------------------------------------
# Time range extraction
# ---------------------------------------------------------------------------
TIME_PATTERNS = [
    (r"last\s+(\d+)\s*(m|min|mins|minute|minutes)\b", lambda m: f"now()-{m.group(1)}m"),
    (r"last\s+(\d+)\s*(h|hr|hrs|hour|hours)\b", lambda m: f"now()-{m.group(1)}h"),
    (r"last\s+(\d+)\s*(d|day|days)\b", lambda m: f"now()-{m.group(1)}d"),
    (r"past\s+(\d+)\s*(m|min|mins|minute|minutes)\b", lambda m: f"now()-{m.group(1)}m"),
    (r"past\s+(\d+)\s*(h|hr|hrs|hour|hours)\b", lambda m: f"now()-{m.group(1)}h"),
    (r"past\s+(\d+)\s*(d|day|days)\b", lambda m: f"now()-{m.group(1)}d"),
    (r"\blast\s+15\s*m", lambda m: "now()-15m"),
    (r"\blast\s+hour\b", lambda m: "now()-1h"),
    (r"\blast\s+24\s*h", lambda m: "now()-24h"),
    (r"\btoday\b", lambda m: "now()-24h"),
    (r"\byesterday\b", lambda m: "now()-48h"),
    (r"\blast\s+week\b", lambda m: "now()-7d"),
]


def extract_time_range(text: str) -> Tuple[Optional[str], str]:
    """Return (dql_time_expression, remaining_text)."""
    for pattern, builder in TIME_PATTERNS:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            time_expr = builder(m)
            cleaned = (text[: m.start()] + text[m.end() :]).strip()
            return time_expr, cleaned
    return None, text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\-\.\:]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _extract_trace_id(text: str) -> Optional[str]:
    # Common trace id patterns (32 hex, or with dashes)
    m = re.search(r"\b([a-f0-9]{16,32})\b", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"trace[_\s-]?id[:\s]+([a-f0-9\-]+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _extract_service(text: str) -> Optional[str]:
    # Look for "service X", "in X", "for X service", or known-looking names
    patterns = [
        r"(?:service|svc)\s+([a-z0-9\-_]+)",
        r"(?:in|for|on)\s+([a-z0-9\-_]+(?:service|svc)?)",
        r"\b([a-z]+(?:service|svc))\b",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            name = m.group(1)
            # Avoid common words
            if name.lower() not in {"service", "logs", "error", "problem", "trace", "span", "last", "hour"}:
                return name
    return None


def _extract_keyword(text: str, stopwords: set) -> Optional[str]:
    words = re.findall(r"[a-z0-9\-_\.]+", text.lower())
    candidates = [w for w in words if w not in stopwords and len(w) > 2]
    if candidates:
        # Prefer longer / more specific tokens
        candidates.sort(key=len, reverse=True)
        return candidates[0]
    return None


# ---------------------------------------------------------------------------
# Intent detectors + builders
# ---------------------------------------------------------------------------

STOPWORDS = {
    "show", "me", "get", "find", "list", "fetch", "give", "display", "all", "the",
    "logs", "log", "error", "errors", "problem", "problems", "active", "open",
    "closed", "span", "spans", "trace", "traces", "for", "in", "of", "from",
    "last", "past", "hour", "hours", "minute", "minutes", "day", "days", "today",
    "yesterday", "related", "containing", "with", "about", "please", "can", "you",
}


def build_logs_dql(
    time_expr: Optional[str],
    service: Optional[str],
    severity: Optional[str],
    keyword: Optional[str],
    trace_id: Optional[str],
) -> str:
    parts = []
    if time_expr:
        parts.append(f"fetch logs, from:{time_expr}")
    else:
        parts.append("fetch logs, from:now()-2h")

    filters = []
    if severity:
        filters.append(f'loglevel == "{severity.upper()}"')
    if service:
        filters.append(f'matchesPhrase(content, "{service}") or matchesPhrase(dt.entity.service, "{service}")')
    if keyword:
        filters.append(f'matchesPhrase(content, "{keyword}")')
    if trace_id:
        filters.append(f'matchesPhrase(content, "{trace_id}") or trace_id == "{trace_id}"')

    if filters:
        parts.append("filter " + " and ".join(filters))

    parts.append("sort timestamp desc")
    parts.append("limit 50")
    return " | ".join(parts)


def build_problems_dql(
    time_expr: Optional[str],
    status: Optional[str],
    keyword: Optional[str],
    service: Optional[str],
) -> str:
    parts = []
    if time_expr:
        parts.append(f"fetch dt.davis.problems, from:{time_expr}")
    else:
        parts.append("fetch dt.davis.problems, from:now()-24h")

    filters = []
    if status:
        filters.append(f'event.status == "{status.upper()}"')
    if keyword:
        filters.append(f'matchesPhrase(event.name, "{keyword}") or matchesPhrase(event.short_name, "{keyword}")')
    if service:
        filters.append(f'matchesPhrase(event.name, "{service}")')

    if filters:
        parts.append("filter " + " and ".join(filters))

    parts.append("sort timestamp desc")
    parts.append("limit 30")
    return " | ".join(parts)


def build_spans_dql(
    time_expr: Optional[str],
    trace_id: Optional[str],
    service: Optional[str],
    error_only: bool = False,
) -> str:
    parts = []
    if time_expr:
        parts.append(f"fetch spans, from:{time_expr}")
    else:
        parts.append("fetch spans, from:now()-2h")

    filters = []
    if trace_id:
        filters.append(f'trace.id == "{trace_id}" or matchesPhrase(content, "{trace_id}")')
    if service:
        filters.append(f'matchesPhrase(service.name, "{service}") or matchesPhrase(dt.entity.service, "{service}")')
    if error_only:
        filters.append('status == "ERROR" or http.status_code >= 400')

    if filters:
        parts.append("filter " + " and ".join(filters))

    parts.append("sort start_time desc")
    parts.append("limit 50")
    return " | ".join(parts)


def build_events_dql(time_expr: Optional[str], keyword: Optional[str]) -> str:
    parts = []
    if time_expr:
        parts.append(f"fetch events, from:{time_expr}")
    else:
        parts.append("fetch events, from:now()-4h")
    if keyword:
        parts.append(f'filter matchesPhrase(event.name, "{keyword}") or matchesPhrase(content, "{keyword}")')
    parts.append("sort timestamp desc")
    parts.append("limit 30")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------------

def nlp_to_dql(text: str) -> NlpResult:
    """
    Convert natural language to DQL.
    Always returns a best-effort query (fallback=True when confidence is low).
    """
    original = text.strip()
    if not original:
        return NlpResult(
            dql='fetch logs, from:now()-1h | limit 10',
            intent="empty",
            confidence=0.0,
            explanation="Empty input – returning recent logs as default.",
            fallback=True,
        )

    cleaned = _clean(original)
    time_expr, remaining = extract_time_range(cleaned)
    remaining = remaining.strip()

    trace_id = _extract_trace_id(original)  # keep original case for hex
    service = _extract_service(remaining)
    keyword = _extract_keyword(remaining, STOPWORDS)

    # ----- Intent detection (ordered by specificity) -----

    # 1. Explicit trace / span
    if re.search(r"\b(trace|span|spans|distributed.?trace)\b", cleaned):
        error_only = bool(re.search(r"\b(error|fail|failed|exception)\b", cleaned))
        dql = build_spans_dql(time_expr, trace_id, service, error_only=error_only)
        return NlpResult(
            dql=dql,
            intent="spans",
            confidence=0.85 if trace_id or service else 0.6,
            explanation="Detected request for spans / traces.",
            filters={"trace_id": trace_id, "service": service, "error_only": error_only},
            time_range=time_expr,
        )

    # 2. Problems / Davis
    if re.search(r"\b(problem|problems|davis|incident|incidents|alert|alerts)\b", cleaned):
        status = None
        if re.search(r"\b(active|open|ongoing)\b", cleaned):
            status = "ACTIVE"
        elif re.search(r"\b(closed|resolved)\b", cleaned):
            status = "CLOSED"
        dql = build_problems_dql(time_expr, status, keyword or service, service)
        return NlpResult(
            dql=dql,
            intent="problems",
            confidence=0.9 if status or keyword or service else 0.7,
            explanation="Detected request for Davis problems.",
            filters={"status": status, "keyword": keyword, "service": service},
            time_range=time_expr,
        )

    # 3. Logs (most common)
    if re.search(r"\b(log|logs|logging|error|errors|exception|exceptions|warn|warning)\b", cleaned) or not remaining:
        severity = None
        if re.search(r"\b(error|errors|exception|severe|fatal)\b", cleaned):
            severity = "ERROR"
        elif re.search(r"\b(warn|warning)\b", cleaned):
            severity = "WARN"
        elif re.search(r"\b(info|information)\b", cleaned):
            severity = "INFO"

        dql = build_logs_dql(time_expr, service, severity, keyword, trace_id)
        conf = 0.8
        if severity or service or trace_id:
            conf = 0.9
        return NlpResult(
            dql=dql,
            intent="logs",
            confidence=conf,
            explanation="Detected request for logs.",
            filters={"service": service, "severity": severity, "keyword": keyword, "trace_id": trace_id},
            time_range=time_expr,
        )

    # 4. Events
    if re.search(r"\b(event|events)\b", cleaned):
        dql = build_events_dql(time_expr, keyword or service)
        return NlpResult(
            dql=dql,
            intent="events",
            confidence=0.75,
            explanation="Detected request for events.",
            filters={"keyword": keyword},
            time_range=time_expr,
        )

    # 5. Fallback – treat as log search with the whole phrase as keyword
    fallback_keyword = keyword or remaining.replace(" ", " ")[:40]
    dql = build_logs_dql(time_expr, service, "ERROR", fallback_keyword, trace_id)
    return NlpResult(
        dql=dql,
        intent="fallback_logs",
        confidence=0.4,
        explanation=f"Could not detect clear intent. Falling back to error log search for '{fallback_keyword}'.",
        filters={"keyword": fallback_keyword, "service": service},
        time_range=time_expr,
        fallback=True,
    )


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def convert(text: str) -> str:
    """Simple helper that returns only the DQL string."""
    return nlp_to_dql(text).dql
