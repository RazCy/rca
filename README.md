# Agentic RCA Agent

Observability Root Cause Analysis agent with:

1. **Dynatrace** → fetch Problems, Culprit, Victims, evidence
2. **OpenSearch** → related spans + logs
3. **Root Cause Report** (Culprit + Victims + analysis)
4. **Natural Language → DQL** (rule-based, no Davis CoPilot scope required)

---

## Features

| Feature | Description |
|---------|-------------|
| **RCA Flow** | Trace ID or Problem ID → Culprit + Victims + full report |
| **Streamlit UI** | Clean web interface with two tabs |
| **NL → DQL** | Type questions in plain English, get DQL + results |
| **No Davis scope needed** | Rule/template based NLP converter |
| **CLI support** | Can also be run from command line |

---

## Project Structure

```
agentic-rca-agent/
├── app.py                      # Streamlit UI
├── config/
│   └── settings.example.env
├── src/
│   ├── agent.py                # Main RCA orchestrator
│   ├── dynatrace_client.py     # Dynatrace Problems + evidence
│   ├── opensearch_client.py    # Spans + Logs
│   ├── nlp_to_dql.py           # Natural Language → DQL
│   └── dql_client.py           # Execute DQL against Grail
├── requirements.txt
└── README.md
```

---

## Setup

```bash
cd agentic-rca-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config/settings.example.env config/settings.env
# Edit config/settings.env with your real values
```

### Required environment variables

```env
# Dynatrace
DYNATRACE_URL=https://your-environment.live.dynatrace.com
DYNATRACE_API_TOKEN=dt0c01.XXXX...

# OpenSearch (optional but recommended for full RCA)
OPENSEARCH_HOSTS=https://localhost:9200
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=admin
OPENSEARCH_USE_SSL=true
OPENSEARCH_VERIFY_CERTS=false
OPENSEARCH_INDEX_TRACES=otel-v1-apm-span-*
OPENSEARCH_INDEX_LOGS=logs-*
```

**Dynatrace token scopes (minimum):**
- `problems.read`
- For DQL: appropriate Grail / storage read scopes (`storage:logs:read`, `storage:events:read`, etc.)

---

## Usage

### 1. Web UI (recommended)

```bash
streamlit run app.py
```

Open the URL shown (usually `http://localhost:8501`).

**Tab 1 – Root Cause Analysis**
- Enter Trace ID and/or Problem ID
- Click **Investigate**
- See Culprit, Victims and full RCA report

**Tab 2 – Natural Language → DQL**
- Type questions in plain English and press Enter
- Examples:
  - `error logs last 2 hours`
  - `error logs for PaymentService`
  - `active problems`
  - `problems related to checkout`
  - `spans for trace 2d45e1cde4f8645b36ab31c07e2e67d6`
  - `logs containing timeout last 1h`

### 2. Command line

```bash
# By Trace ID
python -m src.agent --trace-id 2d45e1cde4f8645b36ab31c07e2e67d6

# By Problem ID
python -m src.agent --problem-id -3923117663478183459_1752459060000V2

# With options
python -m src.agent \
  --trace-id 2d45e1cde4f8645b36ab31c07e2e67d6 \
  --hours 6 \
  --output investigation.json
```

### 3. From Python

```python
from src.agent import RCAAgent
from src.nlp_to_dql import nlp_to_dql

# RCA
agent = RCAAgent()
result = agent.investigate(trace_id="...", lookback_hours=4)
print(result["root_cause_report"])
print(result["dynatrace"]["culprit"])
print(result["dynatrace"]["victims"])

# NL → DQL
from src.nlp_to_dql import nlp_to_dql
res = nlp_to_dql("error logs for PaymentService last 2 hours")
print(res.dql)
print(res.intent, res.confidence)
```

---

## Natural Language → DQL (details)

The converter is **rule/template based** (no external LLM, no Davis CoPilot scope).

It supports:

- Time ranges (`last 15m`, `last 2 hours`, `last 1d`, `today`…)
- Logs + severity (`error`, `warn`, `info`)
- Service filters
- Trace ID filters
- Davis problems (`active`, `closed`)
- Spans / error spans
- Events
- Fallback best-effort queries

You can extend patterns in `src/nlp_to_dql.py`.

---

## Extending further

Possible next steps:

- Add more NL patterns in `nlp_to_dql.py`
- Replace rule-based synthesis with an LLM for richer RCA text
- Wrap the tools for Copilot CLI / LangGraph / CrewAI if you want a fully dynamic agent
- Add metrics queries to the NL → DQL engine

---

## Notes

- Culprit = Davis `rootCauseEntity`
- Victims = affected / impacted entities
- OpenSearch field names vary; the client already tries common variants (`traceId`, `trace_id`, etc.)
- DQL execution uses the Grail query API (with a classic fallback)
