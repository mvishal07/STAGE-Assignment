"""Runtime: one durable agent state per viewer. Decide -> verify -> act -> judge -> learn."""
import heapq, json, os
from collections import Counter, defaultdict
from .util import iso, ts, local_hour, local_day
from .world import EventStore, StoreUnavailable
from .facts import build_facts
from .goals import PRIORITY, eligible, success, PAID_GOALS
from .providers import ModelFailure
from .verifier import verify


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, cfg):
        self.cfg, self.tool, self.model, self.ms = cfg, 0, 0, 0

    def tool_call(self, n=1):
        if self.tool + n > self.cfg.max_tool_calls: raise BudgetExceeded("tool")
        self.tool += n; self.ms += n * self.cfg.tool_ms

    def model_call(self, timeout=False):
        if self.model + 1 > self.cfg.max_model_calls: raise BudgetExceeded("model")
        self.model += 1; self.ms += self.cfg.model_timeout_ms if timeout else self.cfg.model_ms


class Runtime:
    def __init__(self, world, cfg, provider):
        self.world, self.cfg, self.provider = world, cfg, provider
        self.store = EventStore(world.faults)
        self.states, self.decided, self.plans = {}, {}, {}
        self.decisions, self.actions, self.outcomes, self.plan_events, self.security = [], [], [], [], list(world.security)
        self.workers = [0.0] * cfg.workers
        self.breaker = {"fails": 0, "open_until": 0}
        self.counters = Counter()
        self.plan_index = defaultdict(set)
        self.schedule = lambda t, kind, payload: None   # injected by replay engine
        self.n = 0

    # ------------------------------------------------------------------ state
    def state(self, vid):
        if vid not in self.states:
            self.states[vid] = {"viewer_id": vid, "facts": {}, "active_plan": None, "plans": [], "history": [],
                                "actions": [], "outcomes": [], "learned": [], "suppressed_goals": {}, "n_plans": 0}
        return self.states[vid]

    def _hist(self, vid, kind, now, **kw):
        self.state(vid)["history"].append({"t": iso(now), "kind": kind, **kw})

    # ------------------------------------------------------------------ entry
    def handle(self, trig, arrival):
        vid, kind = trig["viewer_id"], trig.get("event", "app_open")
        key = (vid, trig["event_id"] if kind == "app_open" else trig["trigger_id"])
        if key in self.decided:                               # idempotent: duplicate delivery
            self.counters["duplicate_trigger_suppressed"] += 1
            return None
        heapq.heapify(self.workers)
        free = heapq.heappop(self.workers)
        start = max(arrival, free)
        wait = start - arrival
        b = Budget(self.cfg)
        try:
            if wait > self.cfg.drop_wait_s:
                D = self._skeleton(trig, kind, arrival, 0)
                self._noaction(D, "overload_shed", [])
                self.counters["overload_drop"] += 1; b.ms = 5
            else:
                D = self._decide(trig, kind, key, arrival, start, wait, b)
        except BudgetExceeded as ex:
            D = self._skeleton(trig, kind, arrival, 0); self._noaction(D, f"budget_exceeded:{ex}", [])
        finish = start + b.ms / 1000
        heapq.heappush(self.workers, finish)
        D["decided_in_ms"] = int((finish - arrival) * 1000)
        D["decided_at"] = iso(finish)
        D["budget"] = {"model_calls": b.model, "tool_calls": b.tool, "queue_wait_ms": int(wait * 1000)}
        self.decided[key] = D["decision_id"]
        self.decisions.append(D)
        self._record(D, finish)
        return D

    # ------------------------------------------------------------------ decide
    def _skeleton(self, trig, kind, arrival, age):
        self.n += 1
        return {"decision_id": f"D-{self.n:06d}", "viewer_id": trig["viewer_id"],
                "trigger": {"event": kind, "trigger_id": trig["trigger_id"], "event_id": trig.get("event_id"),
                            "received_at": iso(arrival), "event_age_s": int(age)},
                "goal": None, "action": "NO_ACTION", "reason": None, "dialect": {}, "content": [], "confidence": 0.0,
                "confidence_reason": "", "evidence": [], "assumptions": [], "conflicts": [], "downgrades": [],
                "checks": {"passed": [], "failed": []}, "check_details": {}, "judged_by": None, "status": "NO_ACTION",
                "arm": "holdout" if trig["viewer_id"] in self.world.holdout else "treated", "mode": "normal",
                "llm": {"status": "not_called", "rationale": None}}

    def _noaction(self, D, reason, failed, conf=None):
        D.update(action="NO_ACTION", reason=reason, status="NO_ACTION", content=[])
        if failed: D["checks"]["failed"] = failed
        if conf is not None: D["confidence"] = conf
        return D

    def _decide(self, trig, kind, key, arrival, now, wait, b):
        cfg, w = self.cfg, self.world
        vid = trig["viewer_id"]; st = self.state(vid)
        ev = None
        age = 0
        if kind == "app_open":
            ev = self.store.normalise(trig)
            age = max(0, arrival - ev["event_time"])
            if ev["clock_ahead"]: self.counters["device_clock_ahead"] += 1
        D = self._skeleton(trig, kind, arrival, age)
        D["mode"] = "shed" if wait > cfg.shed_wait_s else "normal"
        if D["mode"] == "shed": self.counters["load_shed_mode"] += 1
        if kind == "app_open":
            if age > cfg.stale_trigger_s:
                self.counters["stale_trigger_no_action"] += 1
                return self._noaction(D, f"stale_trigger({int(age)}s): recorded, never acted on", [])
            if age > cfg.presence_s: self.counters["late_trigger_message_only"] += 1
        # ---- understand viewer
        try:
            events = self.store.events(vid, now); b.tool_call()
            prof = w.profiles.get(vid, []); b.tool_call()
        except StoreUnavailable:
            self.counters["store_unavailable_fail_closed"] += 1
            return self._noaction(D, "tool_failure:event_store_unavailable (fail closed)", [], 0.0)
        F, conflicts = build_facts(vid, events, prof, now, cfg, w.catalogue)
        st["facts"] = {k: {x: v[x] for x in ("value", "source", "ref", "age_s") if x in v} for k, v in F.items()
                       if k in ("dialect", "sub_status", "ever_paid", "consent", "signup_ts", "n_plays", "n_completed", "n_opens_7d")}
        dl = F["dialect"]["detail"]
        if not dl:
            return self._noaction(D, "no_dialect_evidence", [])
        D["dialect"] = {"used": dl["used"], "source": dl["source"], "conflicts": dl["conflicts"], "three_way": dl["three_way"],
                        "language": dl["language"], "age_s": dl["age_s"]}
        if dl["three_way"]: self.counters["dialect_three_way_conflict"] += 1
        D["conflicts"] = conflicts
        D["evidence"] = [x for x in [("event:" + trig["event_id"]) if kind == "app_open" else None,
                                     dl["ref"], F["sub_status"]["ref"], "consent:" + str(F["consent"]["source"])] if x]
        # ---- plan continuity (one active plan per viewer)
        plan = st["active_plan"]
        if plan and now > plan["deadline"]:
            self.judge(vid, plan["plan_id"], now, "deadline_passed_at_trigger"); plan = st["active_plan"]
        if plan:
            ok, why, _ = eligible(plan["goal_id"], F, now, w.goals[plan["goal_id"]])
            if not ok:
                self._close_plan(vid, plan, now, "inconclusive", f"abandoned:{why}")
                self.plan_events.append({"t": iso(now), "viewer_id": vid, "plan_id": plan["plan_id"], "event": "revised", "why": why})
                plan = None
        elig_log, goal_id, params = [], None, {}
        if kind == "followup":
            if not plan:
                return self._noaction(D, "followup_without_active_plan", [])
            goal_id, params = plan["goal_id"], plan["params"]
            if local_hour(now, cfg.tz_offset_s) >= cfg.quiet_start or local_hour(now, cfg.tz_offset_s) < cfg.quiet_end:
                if not trig.get("deferred"):
                    nxt = ((now + cfg.tz_offset_s) // 86400 + 1) * 86400 + cfg.quiet_end * 3600 + 300 - cfg.tz_offset_s
                    self.schedule(nxt, "followup", {**trig, "deferred": True, "trigger_id": trig["trigger_id"] + "-d"})
                    self.counters["followup_deferred_quiet_hours"] += 1
                    return self._noaction(D, f"deferred_quiet_hours -> {iso(nxt)}", [])
        elif plan:
            goal_id, params = plan["goal_id"], plan["params"]
        else:
            su = [g for g in PRIORITY if st["suppressed_goals"].get(g, 0) < now]
            for g in su:
                ok, why, p = eligible(g, F, now, w.goals[g]); elig_log.append((g, ok, why, p))
            skipped = [g["id"] + ":" + g["status"] for g in w.goals.values() if g["status"] != "active"]
            el = [(g, p) for g, ok, why, p in elig_log if ok]
            if not el:
                D["assumptions"].append("skipped goals without usable rule/signal: " + ",".join(skipped))
                return self._noaction(D, "no_eligible_goal: " + "; ".join(f"{g}={why}" for g, ok, why, p in elig_log[:4]), [], .7)
            ids = [g for g, _ in el]
            hint = self._llm(vid, F, ids, now, b, D)
            goal_id = hint if hint in ids else ids[0]
            params = dict(el)[goal_id]
        goal = w.goals[goal_id]
        dl_ts = plan["deadline"] if plan else now + goal["deadline_h"] * 3600
        D["goal"] = {"id": goal_id, "name": goal["name"], "deadline": iso(dl_ts), "rule_version": goal["rule_version"],
                     "success_signal": goal["success"], "continuing_plan": bool(plan)}
        D["judged_by"] = {"deadline": iso(dl_ts), "signal": goal["success"]}
        D["evidence"].append(f"goal_rule:{goal_id}-v{goal['rule_version']}")
        D["assumptions"] += [f"profile snapshot may be up to {int((F['sub_status']['age_s'] or 0)/3600)}h stale; purchase events override it",
                             "viewer has not completed the recommended titles (checked from play events)"]
        # ---- candidate actions, smallest safe one wins
        if kind == "followup" or age > cfg.presence_s: cands = ["WHATSAPP", "PUSH"]
        else: cands = ["HOME_PAGE"]
        if dl["three_way"]: cands = [c for c in cands if c == "HOME_PAGE"]
        cons = F["consent"]["value"]    # cheap pre-filter on the decision side; verifier re-checks independently
        pre = [c for c in cands if c == "HOME_PAGE" or cons.get("push" if c == "PUSH" else "whatsapp")]
        if not pre:
            return self._noaction(D, "no_consented_channel_for_message(or three_way_dialect_conflict)", [], .8)
        cands = pre
        D["plan_id"] = plan["plan_id"] if plan else f"P-{vid}-{st['n_plans'] + 1}"
        chosen = None
        for act in cands:
            content = self._content(F, goal_id, params, dl, now, act, plan); b.tool_call()
            Dc = dict(D, action=act, content=content)
            if act == "WHATSAPP" and content:
                Dc["template"] = w.interventions["WHATSAPP"]["templates"].get(w.catalogue[content[0]]["language"])
            if not content or (act == "HOME_PAGE" and len(content) < cfg.min_titles_home):
                D["downgrades"].append({"from": act, "failed": ["insufficient_content_in_dialect"]}); continue
            try:
                b.tool_call(2); res = verify(self, Dc, now)
            except StoreUnavailable:
                self.counters["store_unavailable_fail_closed"] += 1
                return self._noaction(D, "tool_failure:verifier_could_not_read_store (fail closed)", ["verifier_unavailable"], 0.0)
            if res["failed"]:
                D["downgrades"].append({"from": act, "failed": res["failed"], "why": res["details"]})
                for f in res["failed"]: self.counters["verifier_blocked:" + f] += 1
                continue
            chosen = (act, content, res, Dc.get("template")); break
        if not chosen:
            fails = sorted({f for d in D["downgrades"] for f in d["failed"]})
            return self._noaction(D, "all_candidates_failed_verification: " + ",".join(fails), fails, .8)
        act, content, res, tmpl = chosen
        D.update(action=act, content=content, template=tmpl, checks={"passed": res["passed"], "failed": []})
        D["evidence"] = list(dict.fromkeys(D["evidence"] + ["catalogue:" + t for t in content]))
        D["confidence"], D["confidence_reason"] = self._confidence(F, D, age, dl, plan)
        held = vid in w.holdout
        D["status"] = "HOLDOUT_SUPPRESSED" if held else "PUBLISHED"
        if not plan:
            plan = self._new_plan(vid, goal_id, params, now, dl_ts, D)
        plan["exposures"].append({"ts": now, "action": act, "suppressed": held, "decision_id": D["decision_id"]})
        if act == "HOME_PAGE" and not plan["followup_scheduled"]:
            plan["followup_scheduled"] = True
            self.schedule(now + cfg.followup_delay_s, "followup", {"viewer_id": vid, "trigger_id": f"FU-{plan['plan_id']}", "event": "followup"})
        return D

    def _llm(self, vid, F, ids, now, b, D):
        cfg = self.cfg
        if D["mode"] == "shed": D["llm"]["status"] = "skipped_load_shed"; self.counters["llm_skipped_load_shed"] += 1; return None
        if self.breaker["open_until"] > now: D["llm"]["status"] = "skipped_breaker_open"; self.counters["llm_skipped_breaker"] += 1; return None
        summary = {k: F[k]["value"] for k in ("sub_status", "n_plays", "n_completed", "n_opens_7d", "genres")}
        try:
            b.model_call(); out = self.provider.suggest_goal(vid, summary, ids, now)
            self.breaker["fails"] = 0
        except ModelFailure:
            self.breaker["fails"] += 1; self.counters["llm_failure"] += 1
            if self.breaker["fails"] >= 3: self.breaker["open_until"] = now + 120
            D["llm"]["status"] = "failed_fallback_to_rules"; b.ms += cfg.model_timeout_ms - cfg.model_ms
            return None
        D["llm"] = {"status": "ok", "rationale": out.get("rationale")}
        g = out.get("goal")
        if g not in ids:        # generated text is not evidence; the claim must be backed by the rule engine
            self.counters["llm_ineligible_claim_rejected"] += 1
            D["conflicts"].append(f"llm_claimed_{g}_but_rule_engine_says_ineligible: rejected")
            D["llm"]["status"] = "claim_rejected"; return None
        return g

    def _content(self, F, goal_id, params, dl, now, act, plan):
        w, cfg = self.world, self.cfg
        lang_pref = dl["language"]
        genres, played, done = F["genres"]["value"], set(F["titles_played"]["value"]), set(F["completed_titles"]["value"])
        limit = w.interventions[act]["max_titles"] if act != "HOME_PAGE" else 5
        out, roots = [], set()
        req = params.get("series") if goal_id == "G04" else None
        scored = []
        for tid in w.by_dialect[dl["used"]]:
            t = w.catalogue[tid]
            if t["quarantined"] or not (t["from_ts"] <= now <= t["to_ts"]) or tid in done: continue
            if goal_id == "G08" and t["kind"] != "micro_drama": continue
            s = t["popularity"] ** .5 + (2 if t["genre"] in genres else 0)
            if goal_id in ("G01", "G03") and t["kind"] in ("micro_drama", "film"): s += 1.5   # short first win
            if goal_id == "G11" and tid in played: continue
            if tid == req: s += 100
            if t["language"] == lang_pref: s += .5
            scored.append((s, tid))
        for s, tid in sorted(scored, reverse=True):
            t = w.catalogue[tid]; root = t["variant_of"] or tid
            if root in roots: continue
            roots.add(root); out.append(tid)
            if len(out) >= limit: break
        if req and req not in out: return []        # required series unavailable in this dialect: do not substitute
        return out

    def _confidence(self, F, D, age, dl, plan):
        c, why = .45, []
        if (F["sub_status"]["age_s"] or 1e9) < self.cfg.profile_max_age_s: c += .12; why.append("profile<36h")
        else: c -= .05; why.append("profile stale")
        if dl["conflicts"]: c -= .12 * min(2, len(dl["conflicts"])); why.append("dialect sources disagree: " + ",".join(dl["conflicts"]))
        else: c += .1; why.append("dialect sources agree")
        n = F["n_events"]["value"]
        if n >= 5: c += .12; why.append(f"{n} events of history")
        elif n <= 1: c -= .12; why.append("almost no history")
        if age <= 60: c += .08; why.append("fresh trigger")
        if plan: c += .05; why.append("continuing active plan")
        return round(max(.05, min(.95, c)), 2), "; ".join(why)

    # ------------------------------------------------------------------ plans / actions
    def _new_plan(self, vid, goal_id, params, now, deadline, D):
        st = self.state(vid); st["n_plans"] += 1
        p = {"plan_id": D["plan_id"], "viewer_id": vid, "goal_id": goal_id, "params": params, "start": now, "deadline": deadline,
             "rule_version": self.world.goals[goal_id]["rule_version"], "exposures": [], "followup_scheduled": False,
             "arm": D["arm"], "decision_id": D["decision_id"], "status": "active"}
        st["active_plan"] = p; st["plans"].append(p["plan_id"]); self.plans[(vid, p["plan_id"])] = p
        self.plan_index[goal_id].add((vid, p["plan_id"]))
        self.plan_events.append({"t": iso(now), "viewer_id": vid, "plan_id": p["plan_id"], "event": "created", "goal": goal_id})
        self.schedule(deadline + self.cfg.judge_grace_s, "judge", {"viewer_id": vid, "plan_id": p["plan_id"]})
        return p

    def _record(self, D, now):
        vid = D["viewer_id"]; st = self.state(vid)
        self._hist(vid, "decision", now, id=D["decision_id"], action=D["action"], goal=(D["goal"] or {}).get("id"), reason=D["reason"])
        if D["status"] in ("PUBLISHED", "HOLDOUT_SUPPRESSED"):
            a = {"action_id": "A-" + D["decision_id"][2:], "decision_id": D["decision_id"], "viewer_id": vid, "plan_id": D["plan_id"],
                 "action": D["action"], "content": D["content"], "ts": now, "is_message": D["action"] in ("PUSH", "WHATSAPP"),
                 "suppressed": D["status"] == "HOLDOUT_SUPPRESSED", "template": D.get("template")}
            st["actions"].append(a)
            if not a["suppressed"]: self.actions.append({**a, "ts": iso(now)})

    # ------------------------------------------------------------------ close the loop
    def on_event(self, e, now):
        n = self.store.ingest(e)
        if n is None:
            self.counters["duplicate_event_ignored"] += 1; return
        vid = n["viewer_id"]; st = self.states.get(vid)
        if not st: return
        plan = st["active_plan"]
        if plan and plan["start"] <= n["event_time"] <= plan["deadline"]:
            if n["type"] == "purchase" and plan["goal_id"] not in PAID_GOALS:
                self.counters["plan_revised_after_purchase"] += 1
                self._close_plan(vid, plan, now, "inconclusive", "superseded_by_purchase")
                self.plan_events.append({"t": iso(now), "viewer_id": vid, "plan_id": plan["plan_id"], "event": "revised", "why": "purchase_event"})
            elif n["type"] in ("play", "purchase"):
                self.judge(vid, plan["plan_id"], now, "new_event", early=True)
        else:   # late event inside an already-judged window: re-judge (cheap, per viewer)
            for pid in st["plans"][-3:]:
                p = self.plans[(vid, pid)]
                if p["status"] in ("failed", "inconclusive_closed") and p["start"] <= n["event_time"] <= p["deadline"] and n["type"] in ("play", "purchase"):
                    self.counters["late_event_rejudge"] += 1; self.judge(vid, pid, now, "late_event_rejudge", early=True, force=True)

    def judge(self, vid, plan_id, now, reason, early=False, force=False):
        st = self.state(vid); plan = self.plans[(vid, plan_id)]
        if plan["status"] != "active" and not force: return None
        try:
            evs = self.store.events(vid, now)
        except StoreUnavailable:
            self.schedule(now + 600, "judge", {"viewer_id": vid, "plan_id": plan_id}); self.counters["judge_deferred_store_down"] += 1; return None
        goal = self.world.goals[plan["goal_id"]]
        win = [x for x in evs if plan["start"] <= x["event_time"] <= plan["deadline"]]
        ok, sig = success(plan["goal_id"], goal["rule_version"], win, plan, self.world.catalogue)
        if ok:
            status, why = "succeeded", "success_signal_observed"
        elif early:
            return None
        elif goal["status"] != "active":
            status, why = "inconclusive", f"goal_{goal['status']}"
        elif any(x["clock_ahead"] and abs(x["event_time"] - plan["deadline"]) < 600 for x in evs):
            status, why = "inconclusive", "clock_skew_near_deadline_boundary"
        else:
            status, why = "failed", "no_success_signal_before_deadline"
        return self._close_plan(vid, plan, now, status, why, sig, goal["rule_version"], evs, reason)

    def _close_plan(self, vid, plan, now, status, why, sig=None, rv=None, evs=None, via="direct"):
        st = self.state(vid)
        prior = [o for o in self.outcomes if o["plan_id"] == plan["plan_id"]]
        real = [x for x in plan["exposures"] if not x["suppressed"]]
        attributable, a_why = False, "no_exposure(holdout_or_none)"
        if status == "succeeded" and real:
            sev = next((x for x in (evs or self.store.by_viewer[vid]) if x["event_id"] == sig), None)
            lag = (sev["event_time"] - real[0]["ts"]) if sev else 0
            if sev and sev["event_time"] < real[0]["ts"]: attributable, a_why = False, "success_before_first_exposure"
            elif lag < 300 and sev["type"] == "purchase": attributable, a_why = False, "purchase_within_5min_of_exposure(already_in_checkout)"
            else: attributable, a_why = True, "success_after_exposure"
        elif status == "succeeded": a_why = "success_without_exposure(not credited to agent)"
        o = {"outcome_id": f"O-{plan['plan_id']}" + (f"-r{len(prior)}" if prior else ""), "viewer_id": vid, "plan_id": plan["plan_id"],
             "goal_id": plan["goal_id"], "rule_version": rv or self.world.goals[plan["goal_id"]]["rule_version"], "status": status,
             "reason": why, "signal_event": sig, "attributable_to_agent": attributable, "attribution_note": a_why,
             "arm": plan["arm"], "n_exposures": len(real), "judged_at": iso(now), "judged_via": via,
             "window": [iso(plan["start"]), iso(plan["deadline"])], "revision": len(prior)}
        self.outcomes.append(o); st["outcomes"].append(o["outcome_id"])
        plan["status"] = {"succeeded": "succeeded", "failed": "failed", "inconclusive": "inconclusive_closed"}[status]
        if st["active_plan"] is plan: st["active_plan"] = None
        # learning feeds back into the viewer's state and future goal choice
        last = [self.plans[(vid, p)]["status"] for p in st["plans"] if self.plans[(vid, p)]["goal_id"] == plan["goal_id"]][-2:]
        if status == "failed" and len(last) == 2 and all(x == "failed" for x in last):
            st["suppressed_goals"][plan["goal_id"]] = now + 7 * 86400
            st["learned"].append(f"{plan['goal_id']} failed twice with real exposure; pause this goal 7d (abstain rather than nag)")
        else:
            st["learned"].append(f"{plan['goal_id']} {status}: {why}")
        self._hist(vid, "outcome", now, plan=plan["plan_id"], status=status, why=why)
        return o

    def apply_rule_update(self, upd, now):
        """Corrected success rule: re-evaluate ONLY plans on that goal (via index), not the population."""
        g = self.world.goals[upd["goal_id"]]
        g["rule_version"] = upd["rule_version"]; g["success"] = upd["success"]
        affected = sorted(self.plan_index[upd["goal_id"]])
        self.counters["rule_update_plans_reevaluated"] += len(affected)
        self.counters["rule_update_population_skipped"] += max(0, len(self.states) - len({v for v, _ in affected}))
        flipped = 0
        for vid, pid in affected:
            p = self.plans[(vid, pid)]
            if p["status"] == "active": self.judge(vid, pid, now, "rule_update", early=True)
            else:
                prev = [o for o in self.outcomes if o["plan_id"] == pid][-1]["status"]
                evs = self.store.by_viewer[vid]
                win = [x for x in evs if p["start"] <= x["event_time"] <= p["deadline"]]
                ok, sig = success(p["goal_id"], g["rule_version"], win, p, self.world.catalogue)
                new = "succeeded" if ok else ("failed" if prev != "inconclusive" else prev)
                if new != prev and prev != "inconclusive":
                    flipped += 1; self.judge(vid, pid, now, "rule_update_rejudge", force=True)
        self.counters["rule_update_outcomes_flipped"] += flipped
        self.plan_events.append({"t": iso(now), "event": "rule_update", "goal": upd["goal_id"], "plans": len(affected), "flipped": flipped})

    # ------------------------------------------------------------------ persistence
    def flush(self, out):
        os.makedirs(os.path.join(out, "viewer_state"), exist_ok=True)
        wl = lambda n, rows: open(os.path.join(out, n), "w").write("".join(json.dumps(x, default=str) + "\n" for x in rows))
        wl("decisions.jsonl", self.decisions); wl("actions.jsonl", self.actions); wl("outcomes.jsonl", self.outcomes)
        wl("plan_events.jsonl", self.plan_events); wl("security_events.jsonl", self.security)
        for vid, st in self.states.items():
            s = dict(st); s["active_plan"] = st["active_plan"]
            s["decision_ids"] = [d["decision_id"] for d in self.decisions if d["viewer_id"] == vid] if len(self.states) < 300 else "see decisions.jsonl"
            json.dump(s, open(os.path.join(out, "viewer_state", f"{vid}.json"), "w"), default=str, indent=1)
