# Final report

Viewers with an agent: 4703; decisions: 22793; replay wall time 45.7s.

## Decisions
- by action: {'HOME_PAGE': 10636, 'NO_ACTION': 8548, 'PUSH': 1431, 'WHATSAPP': 2178}
- by status: {'HOLDOUT_SUPPRESSED': 2224, 'PUBLISHED': 12021, 'NO_ACTION': 8548}
- reasons for NO_ACTION: {'all_candidates_failed_verification': 1920, 'no_eligible_goal': 1436, 'followup_without_active_plan': 986, 'no_consented_channel_for_message': 983, 'deferred_quiet_hours -> 2026-09-10T02': 790, 'stale_trigger': 659, 'deferred_quiet_hours -> 2026-09-12T02': 572, 'deferred_quiet_hours -> 2026-09-11T02': 560, 'deferred_quiet_hours -> 2026-09-09T02': 373, 'deferred_quiet_hours -> 2026-09-13T02': 247}

## Outcomes (plans judged)
- {'succeeded': 1417, 'inconclusive': 463, 'failed': 3817}; attributable to agent: 1269

## Ground-truth policy audit (independent simulator, published actions only)
- published actions audited: 12021; violations: none

## Surprise events: observed handling
| Surprise | Evidence from this run |
|---|---|
| Buffered/old opens | 659 stale triggers recorded as NO_ACTION; 164 late-but-recent limited to message channels |
| Duplicate / late delivery | 439 duplicate triggers suppressed, 439 duplicate events ignored, 57 late events re-judged an outcome |
| Purchase after decision | 123 active plans revised after a purchase event; profile status overridden by newer purchase |
| Three-way dialect conflict | 48 decisions: app setting wins, messaging disabled, confidence lowered |
| Success rule corrected | 937 plans re-evaluated, 70 outcomes flipped, 3087 viewer-agents untouched |
| LLM claims eligibility without evidence | 49 claims rejected by rule engine and verifier |
| Catalogue prompt injection | 1 title(s) quarantined; text never sent to a model; 0 injected titles shown (0 in audit) |
| Model outage | 5 model failures, 1 calls skipped by circuit breaker; decisions continued rules-only |
| Store outage | 21 decisions failed closed to NO_ACTION; 6 judgements deferred |
| Traffic burst | 357 decisions in load-shed mode (model skipped), 0 safe drops; p95 11829 ms, max 29854 ms, 0 over 5 min |

## Verifier blocks (decision step proposed, independent checks rejected)
- {"duplicate_action": 1345, "quiet_hours": 71, "template": 164, "frequency_cap": 101}

## Headline impact
- Uplift vs randomized holdout: +12.7 pp (95% CI +8.9 to +16.4); naive served-vs-rest would claim +12.0 pp. See impact_report.md.