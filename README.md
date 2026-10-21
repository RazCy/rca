# Agentic RCA Agent

Sequence of flows for Root Cause Analysis:

1. **Dynatrace SDK** (`dt` package) → fetch Problems / evidence by Trace ID or Problem ID  
2. **OpenSearch SDK** (`opensearch-py`) → retrieve related spans + logs  
3. Synthesize a root-cause report

## Project Structure

```
agentic-rca-agent/
├── config/
│   └── settings.example.env
├── src/
│   ├── dynatrace_client.py
│   ├── opensearch_client.py
│   └── agent.py
├── requirements.txt
└── README.md
```

## Setup

```bash
cd agentic-rca-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config/settings.example.env config/settings.env
# Edit config/settings.env with your real values
```

### Dynatrace Authentication

- Uses **API Token** (classic Environment API).  
- Client certificates are **not supported** by the Problems API.  
- Recommended: store the token in Dynatrace Credential Vault when running inside Apps / Extensions / Automations and inject it via environment variable.

Required token scopes:
- `problems.read`
- (optional) `entities.read`, `slo.read`, etc.

### OpenSearch Authentication

- Basic auth via `OPENSEARCH_USER` / `OPENSEARCH_PASSWORD`.  
- TLS settings controlled by `OPENSEARCH_USE_SSL` and `OPENSEARCH_VERIFY_CERTS`.

## Usage

```bash
# Investigate by Trace ID
python -m src.agent --trace-id 2d45e1cde4f8645b36ab31c07e2e67d6

# Investigate by Dynatrace Problem ID
python -m src.agent --problem-id -3923117663478183459_1752459060000V2

# Both + custom lookback + JSON dump
python -m src.agent \
  --trace-id 2d45e1cde4f8645b36ab31c07e2e67d6 \
  --hours 6 \
  --output investigation.json
```

## Extending to a full Agentic system (Copilot CLI / LangGraph / CrewAI)

The current `agent.py` is a clean sequential skeleton.  
You can wrap the three tools (`DynatraceClient`, `OpenSearchClient`, synthesis) as tools for:

- GitHub Copilot CLI / Copilot SDK
- LangGraph / LangChain
- CrewAI / AutoGen
- OpenSearch’s own plan-execute-reflect agent

Example tool signatures ready for an LLM agent:

```python
def fetch_dynatrace_problem(trace_id: str = None, problem_id: str = None) -> dict: ...
def get_trace_spans(trace_id: str) -> list: ...
def find_related_logs(trace_id: str) -> list: ...
def search_opensearch(index: str, query: dict) -> dict: ...
```

## Notes

- The synthesis step is currently rule-based so the skeleton works offline.  
  Replace `_synthesize_root_cause` with an LLM call for production-grade reasoning.
- Field names in OpenSearch indices vary (`traceId` vs `trace_id` vs nested attributes).  
  The queries already try several common variants.
- For production, add proper error handling, retries, and rate-limit handling.