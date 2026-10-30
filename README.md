# KITE

KITE evaluates enterprise knowledge Q&A agents for answer quality, evidence use, access boundaries, and prompt injection resistance. It includes a small knowledge base, evaluation scenarios, a LightRAG REST adapter, and a batch runner.

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

Fill in the two API keys in `.env`. Start each knowledge profile in its own terminal:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_profile.ps1 baseline
powershell -ExecutionPolicy Bypass -File scripts/start_profile.ps1 injection
powershell -ExecutionPolicy Bypass -File scripts/start_profile.ps1 public
```

In another terminal, import the matching documents once into each server:

```powershell
python -m kite.lightrag ingest baseline --url http://127.0.0.1:9621
python -m kite.lightrag ingest injection --url http://127.0.0.1:9622
python -m kite.lightrag ingest public --url http://127.0.0.1:9623
```

Ask a single question or run the full scenario suite:

```powershell
python -m kite.lightrag query "现在市内交通单次报销上限是多少？" --role employee --profile baseline
python -m kite run --out runs/first-batch
```

The single query trace is saved to `runs/trace.json`; a batch run writes `manifest.json` and one result per scenario. The manifest records source hashes, the LightRAG version, model IDs, and query settings without API keys. Each LightRAG reference retains its source path and retrieved chunks. KITE maps a reference to a document ID only when its filename matches a fixture ID; internal tool calls remain marked unobservable.

## Score answer quality

Install OpenJudge in the same environment, then score a saved run:

```powershell
.\.venv\Scripts\python -m pip install py-openjudge==0.2.4
.\.venv\Scripts\python -m kite.quality runs/first-batch --out runs/first-quality
```

The scorer checks expected document coverage, extra and expired references, and required facts. OpenJudge evaluates grounding against the returned chunks; a DeepSeek judge classifies answer, refusal, or clarification. `summary.json` separates these metrics by category. `review.jsonl` provides fields for human review; after filling `human_observed_behavior` or `human_grounded`, pass that file with `--labels` to measure judge disagreement. Use `--offline` for checks that need no model calls. Grounding against returned chunks does not establish document access or prove that each individual citation supports its claim.
