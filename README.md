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

The role metadata describes the expected access policy; it does not imply that LightRAG enforces that policy. KITE's safety scorer evaluates the observed behavior.

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

## Score safety boundaries

Score the same saved run without calling the agent again:

```powershell
.\.venv\Scripts\python -m kite.safety runs/first-batch --out runs/first-safety
```

`summary.json` keeps access violations separate from answer quality. A `confirmed` reference exposure means the RAG API returned a fixture document outside the caller's role; it does not imply the end user saw that reference. Answer disclosure and prompt injection outcomes use a model judge, retain their answer quote and reasoning, and are marked `judge_supported`, `suspected`, or `unknown` as appropriate. `review.jsonl` lists cases for human decisions. Internal tool behavior is `unobservable` for this LightRAG adapter. Use `--offline` to check returned references without a judge call.

## Apply a runtime boundary

KITE's `guarded_query` accepts a role supplied by an authenticated host application. It reads LightRAG's actual document inventory before querying, blocks a mixed or unknown index, then checks returned references and the final answer before release. `block` and `review` withhold the original answer. Decisions are appended to an audit file; raw traces for reviewer access are saved beside it in `policy-traces/`. Restrict access to both paths, and keep the LightRAG server private so clients cannot bypass this gate.

To make the baseline corpus usable for an employee, create a separate empty index and import only that role's documents:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_profile.ps1 baseline -Role employee -Port 9624
.\.venv\Scripts\python -m kite.lightrag ingest baseline --role employee --url http://127.0.0.1:9624
```

Call the gate from the authenticated application, passing its verified role:

```python
from pathlib import Path
from kite.policy import guarded_query

result = guarded_query(question, authenticated_role, "baseline", "http://127.0.0.1:9624", Path("runs/policy-audit.jsonl"))
```

An agent with a retrieval hook can use `filter_documents` before search. A tool adapter can use `check_tool` with an exact role, tool, and argument allowlist before execution. LightRAG's internal tool calls remain outside this adapter's control. The answer check uses a model judge and can require review; the document boundary depends on a dedicated index that cannot be modified concurrently by an untrusted client.

## Repeat and compare evaluations

Run selected high-value scenarios three times. Attack scenarios automatically include their clean control:

```powershell
.\.venv\Scripts\python -m kite.repeat run --out runs/repeat-a --scenario directory-injection --scenario public-feedback-timing
.\.venv\Scripts\python -m kite.repeat compare runs/repeat-a runs/repeat-b --out runs/comparison.md
```

Each attempt has its own Agent trace, quality score, and safety score. `summary.json` reports single-run and all-run pass rates, category counts, observed server time, and versions. A pass requires both quality and safety checks; uncertain judge outcomes stay `unknown`. The comparison report lists added and resolved failures, safety changes, and representative traces. It marks scorer or scenario changes and Agent/API errors as requiring review. Token cost is unobservable with this adapter.

## Diagnose and test one improvement

Generate evidence-linked hypotheses from a repeated run:

```powershell
.\.venv\Scripts\python -m kite.diagnose analyze runs/repeat-a --out runs/diagnosis-a
```

For a candidate, change one main factor in a separate agent config and rerun the same dev and holdout scenarios for both baseline and candidate. `examples/agents/lightrag_employee_scoped.json` demonstrates routing employee queries to the role-scoped index on port 9624. The runner also accepts `--split dev` or `--split holdout` to select a full split.

```powershell
.\.venv\Scripts\python -m kite.diagnose gate runs/base-dev runs/base-holdout runs/candidate-dev runs/candidate-holdout --factor employee_role_scoped_index --out runs/tuning-gate.json
```

The gate requires holdout quality improvement, no dev quality or safety regression, and observed server time within the configured ratio. API errors, unknown judgments, or changed measurement versions require review. It records an `adoptable`, `reject`, or `review` verdict; it never edits the production Agent.
