# Known limitations (intentionally incomplete)

* **Synthetic pack, synthetic simulator.** Uplift numbers demonstrate the measurement method, not real lift. G04's uplift is inflated by simulator design.
* **Verifier independence is partial.** It re-reads raw events but reuses `build_facts` and goal rules. A bug there would fool both. A production system needs a separately implemented policy engine (or property tests against it).
* **One action per decision.** Home page *or* a message, never both; no HERO/IN_APP use. Messages come from follow-ups and late-but-recent opens.
* **Content ranking is simple** (popularity + genre + kind heuristics). No learned recommender; the task is about decisions, not ranking.
* **Timezone fixed to IST**; per-viewer timezone not modelled.
* **Worker queue is simulated in virtual time**, not a real concurrent service; real model latency is not measured. The Anthropic adapter is untested.
* **Instruction-injection detection is regex based** (plus "text never reaches a model"). Novel phrasing would evade the regex, though the structural defence still holds.
* **Judgement uses only play/purchase events.** Watchlist (G09) has no signal and is never chosen. Revenue and churn effects are out of scope.
* **Holdout power is modest per goal;** per-goal rows are descriptive. Repeated plans per viewer are clustered in those rows.
* **Persistence is JSON files** (fine for 5k viewers; not for 10M). See the scaling answer in the discussion notes below.

## Scaling sketch (10M viewers, 40 dialects)
Partition agents by viewer hash; keep hot state in a KV store, events in a log (Kafka/stream) keyed by viewer; precompute per-dialect candidate
pools offline so decision time reads a small slice; run rules first and call a model only for the small ambiguous fraction; shed model calls first
under load; keep dialect-specific templates/policies as data, not code.
