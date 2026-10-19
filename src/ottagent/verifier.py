"""Independent verification. Takes the finished decision, re-reads raw evidence, re-derives facts.
It never trusts the decision's own claims or any generated text. Fails closed."""
from .facts import build_facts
from .goals import eligible
from .util import local_hour, local_day


def verify(rt, D, now):
    cfg, w, vid, act = rt.cfg, rt.world, D["viewer_id"], D["action"]
    passed, failed, det = [], [], {}

    def chk(name, ok, why=""):
        (passed if ok else failed).append(name)
        if not ok: det[name] = why

    events = rt.store.events(vid, now)                      # raises StoreUnavailable -> caller fails closed
    F, _ = build_facts(vid, events, w.profiles.get(vid, []), now, cfg, w.catalogue)
    gid = D["goal"]["id"]
    ok, why, _p = eligible(gid, F, now, w.goals[gid])
    chk("eligibility", ok, why)
    chk("goal_measurable", w.goals[gid]["status"] == "active", w.goals[gid]["status"])
    fd = F["dialect"]["value"]
    chk("dialect", fd is not None and fd == D["dialect"]["used"] and
        all(w.catalogue[t]["dialect"] == fd for t in D["content"]), f"fresh={fd} used={D['dialect']['used']}")
    chk("availability", all(w.catalogue[t]["from_ts"] <= now <= w.catalogue[t]["to_ts"] for t in D["content"]), "outside window")
    chk("no_injection", not any(w.catalogue[t]["quarantined"] for t in D["content"]), "quarantined title")
    roots = [w.catalogue[t]["variant_of"] or t for t in D["content"]]
    chk("no_duplicate_variants", len(roots) == len(set(roots)), "language variants of same title")
    done = set(F["completed_titles"]["value"])
    chk("not_already_watched", not any(t in done for t in D["content"]), "already completed film")
    spec = w.interventions.get(act, {})
    n = len(D["content"])
    if act == "HOME_PAGE":
        chk("min_titles", n >= max(spec["min_titles"], cfg.min_titles_home), f"{n} titles")
    chk("max_titles", n <= spec.get("max_titles", 8), f"{n} titles")
    if act == "HOME_PAGE":
        chk("viewer_present", D["trigger"]["event_age_s"] <= cfg.presence_s, "trigger too old for home page")
    if act in ("PUSH", "WHATSAPP"):
        ch = "push" if act == "PUSH" else "whatsapp"
        chk("consent", F["consent"]["value"].get(ch) is True, f"{ch} consent={F['consent']['value'].get(ch)} src={F['consent']['source']}")
        hr = local_hour(now, cfg.tz_offset_s)
        chk("quiet_hours", not (hr >= cfg.quiet_start or hr < cfg.quiet_end), f"local hour {hr}")
        today = local_day(now, cfg.tz_offset_s)
        sent = [a for a in rt.states[vid]["actions"] if a["is_message"] and local_day(a["ts"], cfg.tz_offset_s) == today]
        chk("frequency_cap", len(sent) < cfg.max_messages_per_day, f"{len(sent)} message(s) today")
        chk("channel_not_three_way_conflict", not F["dialect"]["detail"]["three_way"], "dialect ambiguous: no messages")
        if act == "WHATSAPP":
            lang = F["dialect"]["detail"]["language"] or None
            tmpl = spec["templates"].get(w.catalogue[D["content"][0]]["language"]) if D["content"] else None
            chk("template", tmpl in w.interventions["approved_templates"] and tmpl == D.get("template"), f"template={tmpl}")
    dup = [a for a in rt.states[vid]["actions"] if a["action"] == act and a["plan_id"] == D.get("plan_id") and
           a["content"] == D["content"] and now - a["ts"] < cfg.home_page_dup_window_s and not a["is_message"]]
    chk("duplicate_action", not dup, "same action+content inside dedupe window")
    return {"passed": passed, "failed": failed, "details": det}
