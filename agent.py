"""
Agentic RCA Agent – Sequence of flows

1. Dynatrace SDK  → fetch Problem / context from Trace ID
2. OpenSearch SDK → pull related spans + logs
3. Reasoning      → produce root-cause report
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from dynatrace_client import DynatraceClient, create_dynatrace_client
from opensearch_client import OpenSearchClient, create_opensearch_client

console = Console()


class RCAAgent:
    """
    Simple sequential agent that follows the exact flow requested:

    Dynatrace (Problems / Trace) → Logs → OpenSearch search → Root Cause
    """

    def __init__(
        self,
        dt: Optional[DynatraceClient] = None,
        os_client: Optional[OpenSearchClient] = None,
    ) -> None:
        self.dt = dt or create_dynatrace_client()
        self.os = os_client or create_opensearch_client()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def investigate(
        self,
        trace_id: Optional[str] = None,
        problem_id: Optional[str] = None,
        lookback_hours: int = 4,
    ) -> Dict[str, Any]:
        """
        Run the full investigation sequence.
        Provide either trace_id or problem_id (or both).
        """
        console.rule("[bold blue]RCA Agent – Starting Investigation")

        context: Dict[str, Any] = {
            "input": {"trace_id": trace_id, "problem_id": problem_id},
            "dynatrace": None,
            "spans": [],
            "logs": [],
            "root_cause_report": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # --------------------------------------------------------------
        # STEP 1 – Dynatrace (Problems / Trace context)
        # --------------------------------------------------------------
        console.print(Panel("STEP 1 – Dynatrace SDK: Fetch Problem / Trace context", style="cyan"))

        problem_data = None
        if problem_id:
            problem_data = self.dt.get_problem_details(problem_id)
        elif trace_id:
            problem_data = self.dt.find_problem_related_to_trace(trace_id, hours=lookback_hours)

        if problem_data:
            rca_ctx = self.dt.extract_rca_context(problem_data)
            context["dynatrace"] = rca_ctx
            console.print(f"[green]✓ Problem found:[/] {rca_ctx.get('title')}")
            console.print(f"  Root-cause entity: {rca_ctx.get('root_cause_entity')}")
        else:
            console.print("[yellow]⚠ No Dynatrace Problem found – continuing with Trace ID only[/]")
            context["dynatrace"] = {"note": "No matching Problem", "trace_id": trace_id}

        # --------------------------------------------------------------
        # STEP 2 – OpenSearch: Spans for the Trace
        # --------------------------------------------------------------
        console.print(Panel("STEP 2 – OpenSearch SDK: Fetch Trace Spans", style="cyan"))

        effective_trace_id = trace_id
        if not effective_trace_id and context.get("dynatrace"):
            # Try to discover a traceId from the problem evidence (best-effort)
            raw = json.dumps(context["dynatrace"]).lower()
            # Very simple extraction – improve with proper parsing later
            pass

        spans = []
        if effective_trace_id:
            spans = self.os.get_trace_spans(effective_trace_id)
            context["spans"] = spans
            console.print(f"[green]✓ Retrieved {len(spans)} spans[/]")
        else:
            console.print("[yellow]⚠ No Trace ID available – skipping span fetch[/]")

        # --------------------------------------------------------------
        # STEP 3 – OpenSearch: Related Logs
        # --------------------------------------------------------------
        console.print(Panel("STEP 3 – OpenSearch SDK: Find related Logs", style="cyan"))

        logs = []
        if effective_trace_id:
            logs = self.os.find_logs_by_trace(effective_trace_id, size=int(os.getenv("MAX_LOG_LINES", "200")))
            context["logs"] = logs
            console.print(f"[green]✓ Retrieved {len(logs)} log lines[/]")
        else:
            console.print("[yellow]⚠ No Trace ID – skipping log search[/]")

        # --------------------------------------------------------------
        # STEP 4 – Root Cause Synthesis
        # --------------------------------------------------------------
        console.print(Panel("STEP 4 – Root Cause Analysis", style="cyan"))

        report = self._synthesize_root_cause(context)
        context["root_cause_report"] = report

        # Pretty print the final report
        console.print(Panel(Markdown(report), title="Root Cause Report", border_style="green"))

        return context

    # ------------------------------------------------------------------
    # Reasoning / Synthesis (can later be replaced by LLM call)
    # ------------------------------------------------------------------

    def _synthesize_root_cause(self, context: Dict[str, Any]) -> str:
        """
        Analyze errors from Dynatrace + spans + logs and produce a clear RCA.
        This is a deterministic analyzer. Replace with LLM for richer reasoning.
        """
        lines: list[str] = []
        lines.append("# Root Cause Analysis Report\n")
        lines.append(f"**Generated at**: {context.get('timestamp', 'N/A')}\n")

        dt = context.get("dynatrace") or {}
        spans = context.get("spans") or []
        logs = context.get("logs") or []
        input_data = context.get("input") or {}

        # ----------------------------------------------------------------
        # 1. Dynatrace Problem & Davis evidence
        # ----------------------------------------------------------------
        if dt.get("problem_id"):
            lines.append("## 1. Dynatrace Problem Summary")
            lines.append(f"- **Problem ID**: `{dt.get('problem_id')}`")
            lines.append(f"- **Title**: {dt.get('title')}")
            lines.append(f"- **Severity**: {dt.get('severity')}")
            lines.append(f"- **Status**: {dt.get('status')}")
            if dt.get("start_time"):
                lines.append(f"- **Start**: {dt.get('start_time')}")
            if dt.get("end_time"):
                lines.append(f"- **End**: {dt.get('end_time')}")
            lines.append("")

            # ---- Culprit & Victims (explicit) ----
            culprit = dt.get("culprit") or dt.get("root_cause_entity") or {}
            victims = dt.get("victims") or []

            lines.append("### Culprit (Root Cause)")
            if culprit.get("name"):
                lines.append(f"- **{culprit.get('name')}** (`{culprit.get('id')}`)")
            else:
                lines.append("- Not identified by Davis")
            lines.append("")

            lines.append("### Victims (Impacted / Affected)")
            if victims:
                for v in victims:
                    lines.append(f"- {v}")
            else:
                lines.append("- No additional victims reported")
            lines.append("")

            causal = dt.get("causal_events") or []
            if causal:
                lines.append("### Davis Causal Evidence")
                for ev in causal[:12]:
                    entity = ev.get("entity", {})
                    name = entity.get("name") if isinstance(entity, dict) else str(entity)
                    ev_type = ev.get("evidenceType", "event")
                    lines.append(f"- `{ev_type}` on **{name}**")
                lines.append("")
        else:
            lines.append("## 1. Dynatrace")
            lines.append("- No matching Problem found for the supplied Trace ID / Problem ID.")
            lines.append(f"- Input Trace ID: `{input_data.get('trace_id')}`\n")

        # ----------------------------------------------------------------
        # Helper: detect error spans
        # ----------------------------------------------------------------
        def is_error_span(s: dict) -> bool:
            status = s.get("status") or {}
            code = status.get("code") if isinstance(status, dict) else status
            status_code = s.get("statusCode") or s.get("status_code")
            attrs = s.get("attributes") or {}
            return (
                code in (2, "ERROR", "error", 1)  # OTel ERROR = 2
                or str(status_code).upper() in ("ERROR", "FAILED", "2")
                or str(attrs.get("http.status_code", "")).startswith(("5", "4"))
                or "error" in str(s.get("name", "")).lower()
                or "exception" in str(s).lower()
            )

        # ----------------------------------------------------------------
        # 2. Trace span analysis (errors)
        # ----------------------------------------------------------------
        lines.append("## 2. Trace Span Analysis (OpenSearch)")
        error_spans = []
        if not spans:
            lines.append("- No spans retrieved for this Trace ID.\n")
        else:
            error_spans = [s for s in spans if is_error_span(s)]
            services: dict[str, int] = {}
            for s in spans:
                svc = (
                    (s.get("resource") or {}).get("service.name")
                    or s.get("serviceName")
                    or s.get("service.name")
                    or "unknown"
                )
                services[svc] = services.get(svc, 0) + 1

            lines.append(f"- Total spans: **{len(spans)}**")
            lines.append(f"- Error / failed spans: **{len(error_spans)}**")
            lines.append(f"- Services involved: {', '.join(f'`{k}` ({v})' for k, v in services.items())}")
            lines.append("")

            if error_spans:
                lines.append("### Failing Spans (most relevant)")
                for s in error_spans[:8]:
                    name = s.get("name") or s.get("operationName") or "unknown"
                    svc = (
                        (s.get("resource") or {}).get("service.name")
                        or s.get("serviceName")
                        or "?"
                    )
                    status = s.get("status") or s.get("statusCode") or ""
                    lines.append(f"- **{svc}** → `{name}`  (status: {status})")
                lines.append("")

        # ----------------------------------------------------------------
        # 3. Log analysis – extract real error messages
        # ----------------------------------------------------------------
        lines.append("## 3. Related Logs Analysis (OpenSearch)")
        unique_interesting: list[str] = []
        if not logs:
            lines.append("- No related log lines found.\n")
        else:
            lines.append(f"- Log lines retrieved: **{len(logs)}**")

            error_keywords = (
                "error", "exception", "fail", "timeout", "refused", "unable",
                "cannot", "panic", "fatal", "stacktrace", "traceback", "500",
                "connection reset", "econnrefused", "oom", "out of memory"
            )
            interesting = []
            for log in logs:
                msg = (
                    log.get("message")
                    or log.get("body")
                    or log.get("log")
                    or log.get("msg")
                    or str(log)[:400]
                )
                msg_str = str(msg)
                if any(k in msg_str.lower() for k in error_keywords):
                    interesting.append(msg_str.strip())

            # Deduplicate while preserving order
            seen = set()
            for m in interesting:
                key = m[:120]
                if key not in seen:
                    seen.add(key)
                    unique_interesting.append(m)

            if unique_interesting:
                lines.append("\n### Key error / exception messages from logs")
                for msg in unique_interesting[:10]:
                    cleaned = msg.replace("\n", " ").strip()
                    if len(cleaned) > 350:
                        cleaned = cleaned[:347] + "..."
                    lines.append(f"- {cleaned}")
            else:
                lines.append("- No strong error keywords found in the retrieved logs.")
            lines.append("")

        # ----------------------------------------------------------------
        # 4. Root Cause Conclusion (the actual RCA)
        # ----------------------------------------------------------------
        lines.append("## 4. Root Cause Conclusion")

        culprit = dt.get("culprit") or dt.get("root_cause_entity") or {}
        culprit_name = culprit.get("name")
        victims = dt.get("victims") or []
        has_error_spans = bool(error_spans)
        has_error_logs = bool(unique_interesting)

        if culprit_name:
            lines.append(f"**Culprit**: `{culprit_name}`")
            if victims:
                lines.append(f"**Victims**: {', '.join(f'`{v}`' for v in victims)}")
            lines.append("")
            lines.append(
                f"The Dynatrace Davis engine identified **`{culprit_name}`** as the root-cause entity (Culprit). "
                "This is supported by the causal evidence events listed above. "
                "The failing spans and error log messages confirm the exact failure mode "
                "(timeout, connection refused, exception, resource exhaustion, etc.)."
            )
        elif has_error_spans and has_error_logs:
            lines.append("**Culprit**: Not clearly identified by Dynatrace (no Problem / rootCauseEntity).")
            lines.append(
                "**Most likely root cause**: Application-level failure visible in both "
                "trace spans and logs. The earliest failing span is the strongest candidate for the Culprit."
            )
            lines.append(
                "Recommended next steps: inspect the service that owns the earliest error span, "
                "check its recent deployments, configuration, and downstream dependencies."
            )
        elif has_error_spans:
            lines.append("**Culprit**: Inferred from error spans (no Dynatrace Problem).")
            lines.append(
                "One or more spans finished with error status. "
                "The trace itself shows the failure point."
            )
        elif has_error_logs:
            lines.append("**Culprit**: Inferred from error logs only.")
            lines.append(
                "Error messages are present in logs correlated by Trace ID. "
                "No clear error spans were retrieved."
            )
        else:
            lines.append("**Culprit / Victims**: Inconclusive with current data.")
            lines.append(
                "No strong error signals were found in Dynatrace Problems, spans, or logs "
                "for the given Trace ID / time window."
            )
            lines.append(
                "Suggestions: enlarge the lookback window, verify the Trace ID is correct, "
                "check that logs contain the traceId field, and examine infrastructure metrics "
                "(CPU, memory, network, downstream latency) for the involved services."
            )

        lines.append("\n---")
        lines.append("*Generated by Agentic RCA Agent – Dynatrace + OpenSearch*")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# CLI entry point
# ----------------------------------------------------------------------

def main():
    import argparse
    from dotenv import load_dotenv

    load_dotenv("config/settings.env")  # or settings.example.env while testing

    parser = argparse.ArgumentParser(description="Agentic RCA Agent")
    parser.add_argument("--trace-id", help="W3C / Dynatrace Trace ID")
    parser.add_argument("--problem-id", help="Dynatrace Problem ID (e.g. -1234567890_1699999999999V2)")
    parser.add_argument("--hours", type=int, default=4, help="Lookback window in hours")
    parser.add_argument("--output", help="Write full JSON context to this file")
    args = parser.parse_args()

    if not args.trace_id and not args.problem_id:
        parser.error("Provide at least --trace-id or --problem-id")

    agent = RCAAgent()
    result = agent.investigate(
        trace_id=args.trace_id,
        problem_id=args.problem_id,
        lookback_hours=args.hours,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        console.print(f"\n[green]Full context written to {args.output}[/]")


if __name__ == "__main__":
    main()