# Architecture note

## Ownership
One `viewer_state` object per viewer owns: facts (with provenance), **one** active plan (goal, deadline, rule version, exposures), actions,
outcomes, learnings and suppressed goals. Every trigger, follow-up, judgement and rule update for a viewer goes through that state;
no other component can start a plan. Replay is single-threaded per timeline; in production partition by `hash(viewer_id)` so a viewer's
events are processed by one worker in order (actor model).

## Planning
Goal choice is rule-driven (`goals.eligible`, priority order) over facts with provenance. A model may *suggest* among goals the rule
engine already found eligible; any other suggestion is rejected and logged. A plan is created only when an action is actually decided,
and is **kept** across triggers (no flip-flopping). It is revised only on a change of situation: a purchase, an ineligible re-check,
a goal rule correction, or a deadline. Two failed plans on one goal pause that goal for 7 days (abstain rather than nag).
Underspecified (G07, G10) and unmeasurable (G09) goals are never chosen; this is recorded in the decision.

## Source handling (per-fact precedence, never "newest wins")
* **Dialect**: app setting if observed within 7 d (it is what the viewer will actually see) > profile > campaign link. Disagreements are
  listed in the decision. If all three differ the viewer is "three_way": confidence drops and message channels are disabled (home page only).
* **Subscription status**: profile snapshot, overridden only by a purchase event newer than the snapshot. `ever_paid` from either.
* **Consent**: snapshot, overridden by newer `consent_change` events. Missing = no consent (fail closed). Brand-new viewers have no profile row.
* **Time**: `event_time = min(device_ts, received_at)`; a device clock ahead of server receipt is flagged; judgements near a deadline with a
  skewed event are *inconclusive*.
* **Duplicates**: events de-duplicated by `event_id`; profile duplicates resolved by `(snapshot_at, version)`, conflict kept visible.

## Verification (independent of the decision step)
`verifier.verify` receives the finished decision and re-reads raw events, re-builds facts, and re-checks: eligibility and measurability,
dialect (fresh derivation and per-title), availability window, quarantined titles, language-variant duplicates, already-watched,
min/max titles (<3 titles never shown), viewer presence, consent, quiet hours (22-08 IST), 1 message/day, WhatsApp template approval,
duplicate action. Failure walks a ladder: message -> home page -> no action. Verification failure or store failure never "passes".
Honest limit: it shares `facts.build_facts` and goal rules with the decision step (different call, same code); see KNOWN_LIMITATIONS.

## Memory and measurement
Facts, decisions, actions, outcomes, learnings are persisted per viewer. `outcomes.jsonl` is append-only: a corrected rule or a late event
adds a *revision* rather than editing history. Success is judged from de-duplicated events inside the plan window using the rule version in force;
attribution requires a non-suppressed exposure before the signal (and a purchase within 5 min of exposure is not credited).
Impact: viewer-level randomized holdout fixed before replay. Held-out viewers receive the *same decision*; only publication is suppressed
(and their shadow actions still count against caps) so the arms differ only by intervention. Uplift = treated vs holdout among viewers the
agent decided to act on, on one common 72 h metric, with CI; the naive served-vs-rest number is printed beside it with a balance table.

## Latency and failure recovery
Virtual-time model: service = tool_calls x 35 ms + model_calls x 1.4 s over a worker pool; budgets enforced per decision (8 model / 20 tool).
* queue wait > 20 s: **load-shed mode** (skip model, rules only); > 45 s: safe `NO_ACTION`.
* model failure: fall back to rules; 3 consecutive failures open a 120 s circuit breaker.
* store failure: fail closed to `NO_ACTION`; judgements are retried in 10 min.
* duplicate/late triggers: idempotent on `(viewer, event_id)`; >6 h old = recorded, never acted on; 10 min to 6 h = message channels only.
* rule correction: a goal->plans index re-evaluates only plans on that goal (the run shows ~940 plans re-evaluated, ~3,100 viewers untouched).

## Trust boundaries
Catalogue text is untrusted data: scanned for instruction-like content, offending titles quarantined, text never put in a prompt.
Model output is a hint, never evidence or approval. The pack is personal data: default mock provider; the real adapter sends only a
compact fact summary (no raw events, no catalogue text) and should run on a zero-retention account. The simulator holds hidden truth and is
imported only by the replay harness.
