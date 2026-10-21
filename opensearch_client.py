"""
OpenSearch client using the official opensearch-py SDK.

Authentication priority:
  1. Client Certificate (mTLS)          – OPENSEARCH_CLIENT_CERT + OPENSEARCH_CLIENT_KEY
  2. AWS IAM SigV4                      – AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
  3. Basic auth                         – OPENSEARCH_USER + OPENSEARCH_PASSWORD

All operations are read-only.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from opensearchpy import OpenSearch, RequestsHttpConnection
from rich.console import Console

console = Console()


def _parse_host(hosts_str: str) -> list:
    """Turn a comma-separated host string into the format opensearch-py expects."""
    result = []
    for h in hosts_str.split(","):
        h = h.strip()
        if not h:
            continue
        if "://" in h:
            parsed = urlparse(h)
            host = parsed.hostname or h
            port = parsed.port or (443 if parsed.scheme == "https" else 9200)
        else:
            # host:port or just host
            if ":" in h:
                host, port_str = h.rsplit(":", 1)
                port = int(port_str)
            else:
                host, port = h, 443
        result.append({"host": host, "port": port})
    return result


class OpenSearchClient:
    def __init__(
        self,
        hosts: Optional[str] = None,
        use_ssl: bool = True,
        verify_certs: bool = True,
    ) -> None:
        hosts_str = hosts or os.getenv("OPENSEARCH_HOSTS", "https://localhost:9200")
        self.hosts = _parse_host(hosts_str)

        self.use_ssl = (
            use_ssl
            if use_ssl is not None
            else os.getenv("OPENSEARCH_USE_SSL", "true").lower() == "true"
        )
        self.verify_certs = (
            verify_certs
            if verify_certs is not None
            else os.getenv("OPENSEARCH_VERIFY_CERTS", "true").lower() == "true"
        )

        # --- Auth material ---
        client_cert = os.getenv("OPENSEARCH_CLIENT_CERT")
        client_key = os.getenv("OPENSEARCH_CLIENT_KEY")
        ca_certs = os.getenv("OPENSEARCH_CA_CERTS")

        user = os.getenv("OPENSEARCH_USER")
        password = os.getenv("OPENSEARCH_PASSWORD")

        # AWS IAM related
        aws_region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or os.getenv("OPENSEARCH_AWS_REGION")
        aws_service = os.getenv("OPENSEARCH_AWS_SERVICE", "es")  # "es" for managed, "aoss" for serverless
        # Standard AWS env vars are picked up automatically by boto3:
        # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN

        kwargs: Dict[str, Any] = {
            "hosts": self.hosts,
            "use_ssl": self.use_ssl,
            "verify_certs": self.verify_certs,
            "connection_class": RequestsHttpConnection,
            "timeout": 30,
            "ssl_show_warn": False,
        }

        if ca_certs:
            kwargs["ca_certs"] = ca_certs

        # Priority 1: Client certificates (mTLS)
        if client_cert and client_key:
            console.log("[cyan]OpenSearch[/] Using client certificate authentication (mTLS)")
            kwargs["client_cert"] = client_cert
            kwargs["client_key"] = client_key

        # Priority 2: AWS IAM SigV4
        elif self._has_aws_credentials():
            console.log("[cyan]OpenSearch[/] Using AWS IAM SigV4 authentication")
            try:
                import boto3
                from opensearchpy import AWSV4SignerAuth

                credentials = boto3.Session().get_credentials()
                if not aws_region:
                    # Try to get region from session
                    aws_region = boto3.Session().region_name or "us-east-1"
                    console.log(f"[yellow]OpenSearch[/] AWS region not set, defaulting to {aws_region}")

                auth = AWSV4SignerAuth(credentials, aws_region, aws_service)
                kwargs["http_auth"] = auth
            except ImportError:
                raise ImportError(
                    "boto3 is required for IAM authentication. "
                    "Install it with: pip install boto3"
                )

        # Priority 3: Basic auth
        elif user and password:
            console.log("[cyan]OpenSearch[/] Using basic authentication")
            kwargs["http_auth"] = (user, password)

        else:
            console.log(
                "[yellow]OpenSearch[/] No authentication method configured "
                "(certs / IAM / basic). Connection will likely fail."
            )

        self.client = OpenSearch(**kwargs)

        self.traces_index = os.getenv("OPENSEARCH_INDEX_TRACES", "otel-v1-apm-span-*")
        self.logs_index = os.getenv("OPENSEARCH_INDEX_LOGS", "logs-*")

    @staticmethod
    def _has_aws_credentials() -> bool:
        """Return True if we appear to have usable AWS credentials."""
        # Explicit keys
        if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
            return True
        # Or running in an environment with a role / profile
        if os.getenv("AWS_PROFILE") or os.getenv("AWS_ROLE_ARN") or os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE"):
            return True
        # Or the classic instance metadata / container role (harder to detect cleanly)
        # We still try if OPENSEARCH_USE_IAM is explicitly set
        if os.getenv("OPENSEARCH_USE_IAM", "").lower() in ("1", "true", "yes"):
            return True
        return False

    # ------------------------------------------------------------------
    # Trace related (read-only)
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
    # Log related (read-only)
    # ------------------------------------------------------------------

    def find_logs_by_trace(
        self,
        trace_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        size: int = 200,
    ) -> List[Dict[str, Any]]:
        """Search log indices for entries that contain the given traceId. Read-only."""
        console.log(f"[cyan]OpenSearch[/] Searching logs for traceId={trace_id}")

        if end_time is None:
            end_time = datetime.now(timezone.utc)
        if start_time is None:
            start_time = end_time - timedelta(hours=4)

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

        try:
            resp = self.client.search(index=self.logs_index, body=body)
        except Exception:
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
        """Generic read-only search helper."""
        console.log(f"[cyan]OpenSearch[/] Running custom search on {index}")
        return self.client.search(index=index, body={"size": size, "query": query})


def create_opensearch_client() -> OpenSearchClient:
    return OpenSearchClient()
