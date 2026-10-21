"""
Dynatrace client using the official `dt` Python package (SDK-style).

Authentication is token-based (API Token). Client certificates are not supported
by the Problems / Traces Environment APIs. Tokens can be stored in Dynatrace
Credential Vault when running inside Apps / Extensions / Automations.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from dynatrace import Dynatrace
from rich.console import Console

console = Console()


class DynatraceClient:
    """Thin wrapper around the official `dt` client focused on Problems + evidence."""

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        self.url = (url or os.getenv("DYNATRACE_URL", "")).rstrip("/")
        self.token = token or os.getenv("DYNATRACE_API_TOKEN", "")

        if not self.url or not self.token:
            raise ValueError(
                "DYNATRACE_URL and DYNATRACE_API_TOKEN must be set "
                "(environment or constructor)."
            )

        # Official client
        self.dt = Dynatrace(self.url, self.token)

    # ------------------------------------------------------------------
    # Public API used by the agent
    # ------------------------------------------------------------------

    def get_open_problems(
        self,
        hours: int = 4,
        problem_selector: str = 'status("OPEN")',
        page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return list of recent problems (summary)."""
        console.log(f"[cyan]Dynatrace[/] Fetching open problems (last {hours}h)...")
        problems = list(
            self.dt.problems.list(
                from_=f"now-{hours}h",
                to="now",
                problem_selector=problem_selector,
                page_size=page_size,
            )
        )
        console.log(f"[green]Found {len(problems)} problems[/]")
        return [p.to_json() if hasattr(p, "to_json") else dict(p) for p in problems]

    def get_problem_details(self, problem_id: str) -> Dict[str, Any]:
        """
        Full problem including evidenceDetails (Davis root-cause chain).
        This is the most important call for RCA.
        """
        console.log(f"[cyan]Dynatrace[/] Fetching problem details: {problem_id}")
        problem = self.dt.problems.get(
            problem_id,
            fields="+evidenceDetails,+impactAnalysis,+recentComments",
        )
        data = problem.to_json() if hasattr(problem, "to_json") else dict(problem)
        return data

    def find_problem_related_to_trace(
        self,
        trace_id: str,
        hours: int = 4,
    ) -> Optional[Dict[str, Any]]:
        """
        Heuristic search: look through recent problems for the given traceId.
        Dynatrace often embeds the traceId in title, evidence or entity tags.
        """
        console.log(f"[cyan]Dynatrace[/] Searching problems related to traceId={trace_id}")
        problems = self.get_open_problems(hours=hours)

        trace_id_lower = trace_id.lower()
        for p in problems:
            # Convert whole problem to string for simple contains check
            # (good enough for first version; refine later with proper fields)
            blob = str(p).lower()
            if trace_id_lower in blob:
                problem_id = p.get("problemId") or p.get("problem_id")
                if problem_id:
                    return self.get_problem_details(problem_id)

        console.log("[yellow]No problem found that explicitly mentions the traceId[/]")
        return None

    def extract_rca_context(self, problem: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalise the most useful fields for the downstream agent steps.
        Explicitly extracts:
          - Culprit  = rootCauseEntity (Davis)
          - Victims  = affected / impacted entities
        """
        root_cause = problem.get("rootCauseEntity") or {}
        evidence = (
            problem.get("evidenceDetails", {}).get("details", [])
            if isinstance(problem.get("evidenceDetails"), dict)
            else []
        )

        causal_events = [
            e
            for e in evidence
            if e.get("rootCauseRelevant") is True
        ]

        def _entity_name(ent) -> str:
            if not ent:
                return "unknown"
            if isinstance(ent, dict):
                return ent.get("name") or ent.get("entityId", {}).get("id") or str(ent)
            return str(ent)

        # Culprit = Davis root cause entity
        culprit = {
            "id": (
                root_cause.get("entityId", {}).get("id")
                if isinstance(root_cause.get("entityId"), dict)
                else root_cause.get("entityId")
            ),
            "name": root_cause.get("name"),
        }

        # Victims = affected + impacted entities (deduplicated by name)
        affected = problem.get("affectedEntities") or []
        impacted = problem.get("impactedEntities") or []
        victim_names = []
        seen = set()
        for ent in list(affected) + list(impacted):
            name = _entity_name(ent)
            if name and name not in seen and name != culprit.get("name"):
                seen.add(name)
                victim_names.append(name)

        return {
            "problem_id": problem.get("problemId"),
            "title": problem.get("title"),
            "severity": problem.get("severityLevel"),
            "status": problem.get("status"),
            "start_time": problem.get("startTime"),
            "end_time": problem.get("endTime"),
            # Explicit Culprit / Victim fields
            "culprit": culprit,
            "victims": victim_names,
            # Keep old keys for backward compatibility
            "root_cause_entity": culprit,
            "affected_entities": affected,
            "impacted_entities": impacted,
            "causal_events": causal_events,
            "raw_problem": problem,
        }


# Convenience factory
def create_dynatrace_client() -> DynatraceClient:
    return DynatraceClient()