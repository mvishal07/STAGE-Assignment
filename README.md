# One User, One Agent - OTT dialect platform

A per-viewer lifecycle agent: **understand -> choose one measurable goal -> decide (or abstain) -> verify independently ->
act within budget -> judge from attributable signals -> prove impact against a randomized holdout.**

Pure Python 3.10+, standard library only (pytest for tests). No API key needed: the default model provider is a deterministic mock.

## Run

```bash
cd submission
pip install pytest                                   # tests only
PYTHONPATH=src python -m ottagent.cli all            # generate synthetic pack (5,000 viewers) + replay + reports -> sample_run/
PYTHONPATH=src python -m ottagent.cli replay --pack /path/to/real_pack --out out/   # replay your own pack
cd tests && python -m pytest -q                      # 11 tests
```

Options: `--viewers N`, `--seed S`, `--burst N` (viewers opening in one minute), `--workers N`, `--provider mock|anthropic`.

**Pack format.** The real pack was not available when this was built, so `datagen.py` produces a synthetic stand-in with the same traps
(late/duplicate/skewed events, stale and duplicated profile rows, three-way dialect conflicts, variant titles, an injected catalogue
description, underspecified/unmeasurable goals, a pilot with a naive headline). If your real pack differs in field names, the only
adapter you need is `world.load_pack` and `EventStore.normalise`.

## Layout

| Path | Role |
|---|---|
| `src/ottagent/facts.py` | facts with source/time/freshness, dialect + status + consent precedence, conflicts kept visible |
| `src/ottagent/goals.py` | goal eligibility and (versioned) success rules |
| `src/ottagent/agent.py` | per-viewer state, decide, plan ownership, judge, learn, rule-update handling, persistence |
| `src/ottagent/verifier.py` | independent checks (re-reads raw events, re-derives facts, fails closed) |
| `src/ottagent/providers.py` | mock + optional real-model adapter, circuit-breaker friendly |
| `src/ottagent/replay.py` | virtual-time timeline: triggers, late events, follow-ups, judgements, rule updates |
| `src/ottagent/sim.py` | hidden-truth outcome simulator + independent policy audit (agent never imports it) |
| `src/ottagent/impact.py` | holdout analysis, naive-vs-causal comparison, latency report |
| `sample_run/` | decisions, actions, outcomes, per-viewer state, latency and impact reports from one run |

## Output files

`decisions.jsonl` (every decision incl. NO_ACTION with reason), `actions.jsonl` (published only), `outcomes.jsonl`,
`plan_events.jsonl`, `security_events.jsonl`, `viewer_state/<id>.json`, `latency_report.json`, `impact_report.md`, `final_report.md`, `scoring.json`.
