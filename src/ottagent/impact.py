"""Impact against the randomized holdout (+ why naive served-vs-unserved misleads) and latency report."""
import json, math, os
from collections import Counter, defaultdict
from .util import ts, iso


def _z(p1, n1, p0, n0):
    d = p1 - p0
    se = math.sqrt(p1 * (1 - p1) / max(1, n1) + p0 * (1 - p0) / max(1, n0)) or 1e-9
    pv = 2 * (1 - .5 * (1 + math.erf(abs(d / se) / math.sqrt(2))))
    return d, se, d - 1.96 * se, d + 1.96 * se, pv


def _conv(rt, vid, t0, hours=72):
    for e in rt.store.by_viewer.get(vid, []):
        if t0 <= e["event_time"] <= t0 + hours * 3600 and (e["type"] == "purchase" or (e["type"] == "play" and e.get("completed"))):
            return 1
    return 0


def compute(rt, world, out, pilot):
    D = rt.decisions
    first_act, first_trig = {}, {}
    for d in D:
        t = ts(d["decided_at"]); v = d["viewer_id"]
        first_trig.setdefault(v, t)
        if d["status"] in ("PUBLISHED", "HOLDOUT_SUPPRESSED") and v not in first_act: first_act[v] = t
    res = {}
    # (A) intention-to-treat on a like-for-like population: viewers the agent decided to act on
    arms = {"treated": [], "holdout": []}
    for v, t in first_act.items():
        arms["holdout" if v in world.holdout else "treated"].append(_conv(rt, v, t))
    n1, n0 = len(arms["treated"]), len(arms["holdout"])
    p1, p0 = sum(arms["treated"]) / max(1, n1), sum(arms["holdout"]) / max(1, n0)
    res["itt"] = {"n_treated": n1, "n_holdout": n0, "p_treated": p1, "p_holdout": p0, **dict(zip(("diff", "se", "ci_lo", "ci_hi", "p_value"), _z(p1, n1, p0, n0)))}
    # (B) naive served vs everyone else (what the pilot report did)
    served = {v for v, t in first_act.items() if v not in world.holdout}
    s_conv = [_conv(rt, v, first_act[v]) for v in served]
    o_ids = [v for v in first_trig if v not in served]
    o_conv = [_conv(rt, v, first_trig[v]) for v in o_ids]
    ps, po = sum(s_conv) / max(1, len(s_conv)), sum(o_conv) / max(1, len(o_conv))
    res["naive"] = {"n_served": len(s_conv), "n_other": len(o_conv), "p_served": ps, "p_other": po, "diff": ps - po}
    # balance (pre-period completed plays) between arms of the ITT population
    pre = lambda v: sum(1 for e in rt.store.by_viewer.get(v, []) if e["type"] == "play" and e.get("completed") and e["event_time"] < ts(world.script["replay_start"]))
    bal = {a: sum(pre(v) for v in first_act if (v in world.holdout) == (a == "holdout")) / max(1, len(arms[a])) for a in arms}
    nb = sum(pre(v) for v in first_trig if v not in first_act) / max(1, sum(1 for v in first_trig if v not in first_act))
    res["balance_prior_completed_plays"] = {**bal, "never_acted_on": nb}
    # per-goal plan success (ITT, final outcome per plan, first plan per viewer)
    final = {}
    for o in rt.outcomes: final[o["plan_id"]] = o
    byg = defaultdict(lambda: {"treated": [0, 0], "holdout": [0, 0], "inconclusive": 0})
    for pid, o in final.items():
        if o["status"] == "inconclusive": byg[o["goal_id"]]["inconclusive"] += 1; continue
        a = byg[o["goal_id"]][o["arm"]]; a[0] += 1; a[1] += o["status"] == "succeeded"
    res["per_goal"] = {g: v for g, v in byg.items()}
    res["inconclusive_total"] = sum(1 for o in final.values() if o["status"] == "inconclusive")
    res["pilot"] = pilot
    json.dump(res, open(os.path.join(out, "impact.json"), "w"), indent=1)
    return res


def impact_md(res):
    i, n, p, b = res["itt"], res["naive"], res["pilot"], res["balance_prior_completed_plays"]
    pp = lambda x: f"{100 * x:+.1f} pp"
    ho = p["randomized_holdout"]; pil_true = ho["treated_success"] / max(1, ho["treated_n"]) - ho["holdout_success"] / max(1, ho["holdout_n"])
    L = ["# Impact report", "",
         "**Outcome metric:** viewer completed an episode or bought a plan within 72 h of the agent's first decision to act (the same window is applied to every viewer, so arms are comparable).", "",
         "## 1. Causal estimate: treated vs randomized holdout (same population: viewers the agent decided to act on)", "",
         f"| | n | conversion |", "|---|---|---|",
         f"| Agent acted (treated arm) | {i['n_treated']} | {100 * i['p_treated']:.1f}% |",
         f"| Held out (decision made, action suppressed) | {i['n_holdout']} | {100 * i['p_holdout']:.1f}% |", "",
         f"**Uplift: {pp(i['diff'])}** (95% CI {pp(i['ci_lo'])} to {pp(i['ci_hi'])}, p={i['p_value']:.3f}). "
         "Both arms received an identical agent decision; only publication differed. Assignment is randomized at viewer level, fixed before replay.", "",
         "Balance check, mean pre-replay completed plays per viewer: "
         f"treated {b['treated']:.2f}, holdout {b['holdout']:.2f}, viewers the agent never acted on {b['never_acted_on']:.2f}. "
         "A large gap between the first two would indicate a broken randomization.", "",
         "## 2. Why the simple comparison misleads", "",
         f"Served vs everyone else gives {100 * n['p_served']:.1f}% vs {100 * n['p_other']:.1f}% = **{pp(n['diff'])}**, "
         f"against a randomized effect of {pp(i['diff'])}: the naive number {'overstates' if n['diff'] > i['diff'] else 'understates'} impact by {abs(100 * (n['diff'] - i['diff'])):.1f} pp. "
         "The two groups are not comparable because the agent chooses whom to act on (eligible goal, consent, fresh trigger, dialect content), "
         f"so served viewers differ systematically from the rest (mean prior completed plays {b['treated']:.2f} vs {b['never_acted_on']:.2f}). "
         "The direction of that bias depends on the targeting policy, which is exactly why it cannot be corrected by assumption; only the holdout is clean.", "",
         f"The earlier pilot report has the same flaw: headline uplift {p['headline_uplift_pp']} pp (served vs unserved), "
         f"while its own randomized holdout shows {100 * pil_true:+.1f} pp.", "",
         "## 3. Plan-level goal success by goal (judged outcomes, ITT)", "", "| goal | treated n | treated success | holdout n | holdout success |", "|---|---|---|---|---|"]
    for g, v in sorted(res["per_goal"].items()):
        tn, ts_ = v["treated"]; hn, hs = v["holdout"]
        L.append(f"| {g} | {tn} | {100 * ts_ / max(1, tn):.0f}% | {hn} | {100 * hs / max(1, hn):.0f}% |")
    L += ["", f"Inconclusive plans excluded from rates: {res['inconclusive_total']}.", "",
          "## 4. Caveats", "- Synthetic simulator: effect sizes demonstrate the method, not real-world lift.",
          "- Small holdout per goal: per-goal rows are descriptive, not significant individually.",
          "- Headline estimate uses one unit per viewer (first act). Per-goal rows pool all judged plans, so viewers can repeat (clustered): descriptive only.",
          "- G04 uplift is inflated by simulator design: only a treated viewer is steered to the next episode of the open series."]
    return "\n".join(L)


def latency(rt, tags=None):
    ms = sorted(d["decided_in_ms"] for d in rt.decisions)
    q = lambda p: ms[min(len(ms) - 1, int(p * len(ms)))]
    by = defaultdict(list)
    for d in rt.decisions: by[d["trigger"].get("tag") or "normal"].append(d["decided_in_ms"])
    return {"decisions": len(ms), "p50_ms": q(.5), "p95_ms": q(.95), "p99_ms": q(.99), "max_ms": ms[-1],
            "within_60s_pct": round(100 * sum(1 for m in ms if m <= 60000) / len(ms), 2),
            "over_60s": sum(1 for m in ms if m > 60000), "over_5min_failures": sum(1 for m in ms if m > 300000),
            "max_queue_wait_ms": max(d["budget"]["queue_wait_ms"] for d in rt.decisions),
            "by_trigger_group": {k: {"n": len(v), "p95_ms": sorted(v)[int(.95 * (len(v) - 1))], "max_ms": max(v)} for k, v in by.items()},
            "max_model_calls_used": max(d["budget"]["model_calls"] for d in rt.decisions),
            "max_tool_calls_used": max(d["budget"]["tool_calls"] for d in rt.decisions),
            "note": "virtual time: service time = tool_calls*tool_ms + model_calls*model_ms; queueing across a worker pool"}
