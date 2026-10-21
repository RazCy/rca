"""
Dynatrace DQL (Grail) client – pure requests based.
Works with classic API token or platform token.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests
from rich.console import Console

console = Console()


class DqlClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("DYNATRACE_URL", "")).rstrip("/")
        self.token = token or os.getenv("DYNATRACE_API_TOKEN", "")

        if not self.base_url or not self.token:
            raise ValueError("DYNATRACE_URL and DYNATRACE_API_TOKEN are required for DQL")

        # Grail query endpoint (platform style)
        # Falls back to classic if needed
        self.query_url = f"{self.base_url}/platform/storage/query/v1/query:execute"
        self.poll_url = f"{self.base_url}/platform/storage/query/v1/query:poll"

        self.session = requests.Session()
        # Support both classic Api-Token and Bearer
        if self.token.startswith("dt0") or self.token.startswith("dtc"):
            self.session.headers["Authorization"] = f"Api-Token {self.token}"
        else:
            self.session.headers["Authorization"] = f"Bearer {self.token}"
        self.session.headers["Content-Type"] = "application/json"

    def execute(
        self,
        dql: str,
        max_wait_seconds: int = 60,
        poll_interval: float = 1.5,
    ) -> Dict[str, Any]:
        """
        Execute a DQL query and return the result records.
        Uses the asynchronous Grail query API (start → poll).
        """
        console.log(f"[cyan]DQL[/] Executing:\n{dql}")

        payload = {
            "query": dql,
            "requestTimeoutMilliseconds": 60000,
        }

        # Start query
        try:
            resp = self.session.post(self.query_url, json=payload, timeout=30)
        except requests.RequestException as e:
            # Fallback: try classic /api/v2/dql/query style if platform endpoint fails
            return self._execute_classic_fallback(dql)

        if resp.status_code == 404:
            return self._execute_classic_fallback(dql)

        if resp.status_code >= 400:
            return {
                "error": True,
                "status_code": resp.status_code,
                "message": resp.text[:500],
                "records": [],
            }

        data = resp.json()
        request_token = data.get("requestToken") or data.get("token")

        if not request_token:
            # Some environments return results immediately
            return {
                "error": False,
                "records": data.get("records") or data.get("result", {}).get("records") or [],
                "raw": data,
            }

        # Poll for result
        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            time.sleep(poll_interval)
            poll = self.session.get(
                self.poll_url,
                params={"request-token": request_token},
                timeout=30,
            )
            if poll.status_code >= 400:
                return {
                    "error": True,
                    "status_code": poll.status_code,
                    "message": poll.text[:500],
                    "records": [],
                }

            body = poll.json()
            state = (body.get("state") or body.get("status") or "").upper()

            if state in ("SUCCEEDED", "DONE", "FINISHED"):
                records = (
                    body.get("records")
                    or body.get("result", {}).get("records")
                    or body.get("data", {}).get("records")
                    or []
                )
                return {"error": False, "records": records, "raw": body}

            if state in ("FAILED", "ERROR", "CANCELLED"):
                return {
                    "error": True,
                    "message": body.get("error") or body.get("message") or "Query failed",
                    "records": [],
                    "raw": body,
                }

        return {"error": True, "message": "Query timed out", "records": []}

    def _execute_classic_fallback(self, dql: str) -> Dict[str, Any]:
        """Best-effort fallback for environments that still use older endpoints."""
        url = f"{self.base_url}/api/v2/dql/query"
        try:
            resp = self.session.post(url, json={"query": dql}, timeout=60)
            if resp.status_code >= 400:
                return {
                    "error": True,
                    "status_code": resp.status_code,
                    "message": resp.text[:500],
                    "records": [],
                }
            data = resp.json()
            records = data.get("records") or data.get("result", {}).get("records") or []
            return {"error": False, "records": records, "raw": data}
        except Exception as e:
            return {"error": True, "message": str(e), "records": []}


def create_dql_client() -> DqlClient:
    return DqlClient()
