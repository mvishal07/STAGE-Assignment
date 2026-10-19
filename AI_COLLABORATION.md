# AI collaboration log

> **Edit this before submitting.** The evaluators score whether *you* directed, checked and sometimes rejected AI output. Keep only what is true for you.

## What was done with Claude (this build)
* Claude (chat) read the brief and proposed the architecture below; generated the code, tests, and docs in this repo in one session.
* Design decisions that were deliberately steered: plan creation only on an actual act; holdout viewers get the *same decision* with publication suppressed
  (not "no decision"); ITT analysis on one common metric; append-only outcome revisions for rule corrections; fail-closed on store failure.

## How output was checked
* `pytest` (11 tests: duplicate trigger, stale buffered trigger, three-way dialect conflict, store and model outage, corrected goal rule,
  purchase after decision, LLM hallucination, catalogue injection, consent, verifier disagreement, quiet hours/holdout).
* Full replay of 5,000 viewers / ~22,800 decisions; independent ground-truth audit in `scoring.json` (0 policy violations on published actions).
* Manual read of sample decisions in `sample_run/decisions.jsonl`.

## What you must add yourself (be specific and honest)
1. Which AI suggestions you **rejected or changed**, and why. (The interview asks this directly.) Candidates to consider if they match your judgment:
   scoring candidate titles with the LLM, letting the model approve its own decision, comparing served vs unserved for impact, reprocessing all viewers on a rule change.
2. Bugs you found in generated code (for example: duplicate outcome rows when a deadline judgement fired for an already-closed plan; an early version
   of the impact text that assumed the direction of selection bias) and how you found them.
3. Anything you changed after reading the code.
