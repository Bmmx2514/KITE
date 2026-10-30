# KITE

KITE evaluates enterprise knowledge Q&A agents for answer quality, evidence use, access boundaries, and prompt injection resistance. The repository currently contains the first milestone: a small knowledge base and evaluation scenarios. LightRAG integration follows next.

## Scenario data

- `examples/knowledge/documents.jsonl`: fictional boundary cases and one [EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench) document, with roles and validity dates. The upstream document is MIT licensed; its source URL is recorded in the row.
- `examples/knowledge/profiles.json`: document sets used by each scenario.
- `examples/scenarios/core.jsonl`: questions, expected behavior, evidence, and paired boundary cases.
- `examples/scenarios/enterprise_rag_bench.jsonl`: two upstream benchmark questions as import references. Their source documents are not bundled here and these cases are not part of the runnable core suite. The public-document question in `core.jsonl` is KITE-authored.

Validate the core data with Python 3.10 or newer:

```sh
python scripts/check_scenarios.py
```

The role metadata describes the expected access policy; it does not imply that the test RAG enforces that policy. KITE will evaluate that behavior when the agent adapter is added.
