# Impact report

**Outcome metric:** viewer completed an episode or bought a plan within 72 h of the agent's first decision to act (the same window is applied to every viewer, so arms are comparable).

## 1. Causal estimate: treated vs randomized holdout (same population: viewers the agent decided to act on)

| | n | conversion |
|---|---|---|
| Agent acted (treated arm) | 3665 | 40.0% |
| Held out (decision made, action suppressed) | 670 | 27.3% |

**Uplift: +12.7 pp** (95% CI +8.9 pp to +16.4 pp, p=0.000). Both arms received an identical agent decision; only publication differed. Assignment is randomized at viewer level, fixed before replay.

Balance check, mean pre-replay completed plays per viewer: treated 1.25, holdout 1.28, viewers the agent never acted on 1.87. A large gap between the first two would indicate a broken randomization.

## 2. Why the simple comparison misleads

Served vs everyone else gives 40.0% vs 27.9% = **+12.0 pp**, against a randomized effect of +12.7 pp: the naive number understates impact by 0.6 pp. The two groups are not comparable because the agent chooses whom to act on (eligible goal, consent, fresh trigger, dialect content), so served viewers differ systematically from the rest (mean prior completed plays 1.25 vs 1.87). The direction of that bias depends on the targeting policy, which is exactly why it cannot be corrected by assumption; only the holdout is clean.

The earlier pilot report has the same flaw: headline uplift 10.0 pp (served vs unserved), while its own randomized holdout shows +4.0 pp.

## 3. Plan-level goal success by goal (judged outcomes, ITT)

| goal | treated n | treated success | holdout n | holdout success |
|---|---|---|---|---|
| G01 | 988 | 34% | 197 | 22% |
| G02 | 698 | 12% | 94 | 5% |
| G03 | 1049 | 46% | 193 | 37% |
| G04 | 806 | 23% | 173 | 0% |
| G05 | 191 | 9% | 25 | 4% |
| G06 | 223 | 19% | 31 | 6% |
| G08 | 302 | 3% | 34 | 0% |
| G11 | 84 | 42% | 16 | 19% |
| G12 | 45 | 51% | 14 | 50% |

Inconclusive plans excluded from rates: 458.

## 4. Caveats
- Synthetic simulator: effect sizes demonstrate the method, not real-world lift.
- Small holdout per goal: per-goal rows are descriptive, not significant individually.
- Headline estimate uses one unit per viewer (first act). Per-goal rows pool all judged plans, so viewers can repeat (clustered): descriptive only.
- G04 uplift is inflated by simulator design: only a treated viewer is steered to the next episode of the open series.