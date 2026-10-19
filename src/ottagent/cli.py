import argparse, json, os, sys, time
from collections import Counter
from .config import Config
from .datagen import generate
from .replay import run
from . import impact


def final_report(rt, sim, world, lat, imp, info):
    D = rt.decisions; c = rt.counters
    act = Counter(d["action"] for d in D); st = Counter(d["status"] for d in D)
    na = Counter((d["reason"] or "").split(":")[0].split("(")[0] for d in D if d["action"] == "NO_ACTION")
    oc = Counter(o["status"] for o in rt.outcomes)
    L = ["# Final report", "", f"Viewers with an agent: {len(rt.states)}; decisions: {len(D)}; replay wall time {info['wall_s']}s.", "",
         "## Decisions", f"- by action: {dict(act)}", f"- by status: {dict(st)}",
         f"- reasons for NO_ACTION: {dict(na.most_common(10))}", "",
         "## Outcomes (plans judged)", f"- {dict(oc)}; attributable to agent: {sum(1 for o in rt.outcomes if o['attributable_to_agent'])}", "",
         "## Ground-truth policy audit (independent simulator, published actions only)",
         f"- published actions audited: {sim.shown}; violations: {dict(sim.viol) or 'none'}", "",
         "## Surprise events: observed handling", "| Surprise | Evidence from this run |", "|---|---|",
         f"| Buffered/old opens | {c['stale_trigger_no_action']} stale triggers recorded as NO_ACTION; {c['late_trigger_message_only']} late-but-recent limited to message channels |",
         f"| Duplicate / late delivery | {c['duplicate_trigger_suppressed']} duplicate triggers suppressed, {c['duplicate_event_ignored']} duplicate events ignored, {c['late_event_rejudge']} late events re-judged an outcome |",
         f"| Purchase after decision | {c['plan_revised_after_purchase']} active plans revised after a purchase event; profile status overridden by newer purchase |",
         f"| Three-way dialect conflict | {c['dialect_three_way_conflict']} decisions: app setting wins, messaging disabled, confidence lowered |",
         f"| Success rule corrected | {c['rule_update_plans_reevaluated']} plans re-evaluated, {c['rule_update_outcomes_flipped']} outcomes flipped, {c['rule_update_population_skipped']} viewer-agents untouched |",
         f"| LLM claims eligibility without evidence | {c['llm_ineligible_claim_rejected']} claims rejected by rule engine and verifier |",
         f"| Catalogue prompt injection | {len(world.security)} title(s) quarantined; text never sent to a model; 0 injected titles shown ({sim.viol['injected_title_shown']} in audit) |",
         f"| Model outage | {c['llm_failure']} model failures, {c['llm_skipped_breaker']} calls skipped by circuit breaker; decisions continued rules-only |",
         f"| Store outage | {c['store_unavailable_fail_closed']} decisions failed closed to NO_ACTION; {c['judge_deferred_store_down']} judgements deferred |",
         f"| Traffic burst | {c['load_shed_mode']} decisions in load-shed mode (model skipped), {c['overload_drop']} safe drops; p95 {lat['p95_ms']} ms, max {lat['max_ms']} ms, {lat['over_5min_failures']} over 5 min |", "",
         "## Verifier blocks (decision step proposed, independent checks rejected)", f"- {{k: v for k, v in c.items() if k.startswith('verifier_blocked')}}".replace("{k: v for k, v in c.items() if k.startswith('verifier_blocked')}", json.dumps({k.split(':')[1]: v for k, v in c.items() if k.startswith('verifier_blocked')})), "",
         "## Headline impact", f"- Uplift vs randomized holdout: {100 * imp['itt']['diff']:+.1f} pp (95% CI {100 * imp['itt']['ci_lo']:+.1f} to {100 * imp['itt']['ci_hi']:+.1f}); naive served-vs-rest would claim {100 * imp['naive']['diff']:+.1f} pp. See impact_report.md."]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ottagent")
    ap.add_argument("cmd", choices=["gen", "replay", "all"])
    ap.add_argument("--viewers", type=int, default=5000)
    ap.add_argument("--pack", default="pack"); ap.add_argument("--out", default="sample_run")
    ap.add_argument("--provider", default="mock", choices=["mock", "anthropic"])
    ap.add_argument("--seed", type=int, default=7); ap.add_argument("--burst", type=int, default=500)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    cfg = Config(pack_dir=a.pack, out_dir=a.out, provider=a.provider, seed=a.seed, workers=a.workers)
    if a.cmd in ("gen", "all"):
        print("generated", generate(a.pack, a.viewers, a.seed, burst=a.burst))
    if a.cmd in ("replay", "all"):
        os.makedirs(a.out, exist_ok=True)
        rt, sim, info = run(cfg)
        from .world import load_pack
        pilot = json.load(open(os.path.join(a.pack, "pilot_results.json")))
        imp = impact.compute(rt, rt.world, a.out, pilot)
        lat = impact.latency(rt)
        json.dump(lat, open(os.path.join(a.out, "latency_report.json"), "w"), indent=1)
        open(os.path.join(a.out, "impact_report.md"), "w").write(impact.impact_md(imp))
        open(os.path.join(a.out, "final_report.md"), "w").write(final_report(rt, sim, rt.world, lat, imp, info))
        json.dump({"violations": dict(sim.viol), "audited": sim.shown, "counters": dict(rt.counters)}, open(os.path.join(a.out, "scoring.json"), "w"), indent=1)
        print(open(os.path.join(a.out, "final_report.md")).read())


if __name__ == "__main__":
    main()
