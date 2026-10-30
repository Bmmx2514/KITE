# KITE

KITE evaluates enterprise knowledge Q&A agents for answer quality, evidence use, access boundaries, and prompt injection resistance. It includes a small knowledge base, evaluation scenarios, and a LightRAG REST adapter.

## Scenario data

- `examples/knowledge/documents.jsonl`: fictional boundary cases and one [EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench) document, with roles and validity dates. The upstream document is MIT licensed; its source URL is recorded in the row.
- `examples/knowledge/profiles.json`: document sets used by each scenario.
- `examples/scenarios/core.jsonl`: questions, expected behavior, evidence, and paired boundary cases.
- `examples/scenarios/enterprise_rag_bench.jsonl`: two upstream benchmark questions as import references. Their source documents are not bundled here and these cases are not part of the runnable core suite. The public-document question in `core.jsonl` is KITE-authored.

Validate the core data with Python 3.10 or newer:

```sh
python scripts/check_scenarios.py
```

The role metadata describes the expected access policy; it does not imply that LightRAG enforces that policy. KITE's later scoring stage will evaluate the observed behavior.

## Test with LightRAG

Create a Python virtual environment and install the tested LightRAG server version:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install "lightrag-hku[api]==1.5.7"
Copy-Item .env.example .env
```

Fill in the two API keys in `.env`, then start the server from the repository root:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
New-Item -ItemType Directory -Force .lightrag\logs | Out-Null
.\.venv\Scripts\lightrag-server.exe
```

In another terminal, import one knowledge profile and ask a question:

```powershell
python -m kite.lightrag ingest baseline
python -m kite.lightrag query "现在市内交通单次报销上限是多少？" --role employee --profile baseline
```

The query trace is saved to `runs/trace.json`. Each LightRAG reference retains its source path and retrieved chunks. KITE maps a reference to a document ID only when its filename matches a fixture ID; internal tool calls remain marked unobservable. Use a separate LightRAG working directory for each knowledge profile to keep the corpora isolated.
