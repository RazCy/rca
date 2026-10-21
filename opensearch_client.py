"""
OpenSearch client using the official opensearch-py SDK.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from opensearchpy import OpenSearch, RequestsHttpConnection
from rich.console import Console

console = Console()


class OpenSearchClient:
    def __init__(
        self,
        hosts: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        use_ssl: bool = True,
        verify_certs: bool = False,
    ) -> None:
        hosts_str = hosts or os.getenv("OPENSEARCH_HOSTS", "https://localhost:9200")
        self.hosts = [h.strip() for h in hosts_str.split(",")]
        self.user = user or os.getenv("OPENSEARCH_USER", "admin")
        self.password = password or os.getenv("OPENSEARCH_PASSWORD", "admin")
        self.use_ssl = use_ssl if use_ssl is not None else os.getenv("OPENSEARCH_USE_SSL", "true").lower() == "true"
        self.verify_certs = verify_certs if verify_certs is not None else os.getenv("OPENSEARCH_VERIFY_CERTS", "false").lower() == "true"

        self.client = OpenSearch(
            hosts=self.hosts,
            http_auth=(self.user, self.password),
            use_ssl=self.use_ssl,
            verify_certs=self.verify_certs,
            connection_class=RequestsHttpConnection,
            timeout=30,
        )

        self.traces_index = os.getenv("OPENSEARCH_INDEX_TRACES", "otel-v1-apm-span-*")
        self.logs_index = os.getenv("OPENSEARCH_INDEX_LOGS", "logs-*")

    # ------------------------------------------------------------------
    # Trace related
    # ------------------------------------------------------------------

    def get_trace_spans(self, trace_id: str, size: int = 200) -> List[Dict[str, Any]]:
        """Fetch all spans belonging to a traceId from the OTel span index."""
        console.log(f"[cyan]OpenSearch[/] Fetching spans for traceId={trace_id}")

        body = {
            "size": size,
            "query": {
                "bool": {
                    "should": [
                        {"term": {"traceId": trace_id}},
                        {"term": {"traceId.keyword": trace_id}},
                        {"term": {"trace_id": trace_id}},
                        {"term": {"attributes.trace_id": trace_id}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": [{"startTime": {"order": "asc"}}],
        }

        resp = self.client.search(index=self.traces_index, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        console.log(f"[green]Found {len(hits)} spans[/]")
        return [h["_source"] for h in hits]

    # ------------------------------------------------------------------
    # Log related
    # ------------------------------------------------------------------

    def find_logs_by_trace(
        self,
        trace_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        size: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        Search log indices for entries that contain the given traceId.
        Works with both structured (traceId field) and unstructured logs.
        """
        console.log(f"[cyan]OpenSearch[/] Searching logs for traceId={trace_id}")

        if end_time is None:
            end_time = datetime.now(timezone.utc)
        if start_time is None:
            start_time = end_time - timedelta(hours=4)

        # Build a flexible query that works with common field names
        should_clauses = [
            {"term": {"traceId": trace_id}},
            {"term": {"traceId.keyword": trace_id}},
            {"term": {"trace_id": trace_id}},
            {"term": {"attributes.trace_id": trace_id}},
            {"match_phrase": {"message": trace_id}},
            {"match_phrase": {"body": trace_id}},
            {"match_phrase": {"log": trace_id}},
        ]

        body = {
            "size": size,
            "query": {
                "bool": {
                    "must": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": start_time.isoformat(),
                                    "lte": end_time.isoformat(),
                                }
                            }
                        }
                    ],
                    "should": should_clauses,
                    "minimum_should_match": 1,
                }
            },
            "sort": [{"@timestamp": {"order": "asc"}}],
        }

        # Fallback: some indices use different timestamp fields
        try:
            resp = self.client.search(index=self.logs_index, body=body)
        except Exception:
            # Try without @timestamp constraint if the field is missing
            body["query"]["bool"].pop("must", None)
            resp = self.client.search(index=self.logs_index, body=body)

        hits = resp.get("hits", {}).get("hits", [])
        console.log(f"[green]Found {len(hits)} log lines[/]")
        return [h["_source"] for h in hits]

    def search(
        self,
        index: str,
        query: Dict[str, Any],
        size: int = 50,
    ) -> Dict[str, Any]:
        """Generic search helper (used by the agent for ad-hoc queries)."""
        console.log(f"[cyan]OpenSearch[/] Running custom search on {index}")
        return self.client.search(index=index, body={"size": size, "query": query})


def create_opensearch_client() -> OpenSearchClient:
    return OpenSearchClient()