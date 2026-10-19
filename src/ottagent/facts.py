"""Understand the viewer: facts with source, time, freshness; conflicts stay visible."""
from collections import Counter
from .util import ts


def fact(value, source, ref=None, observed_at=None, now=0):
    return {"value": value, "source": source, "ref": ref, "observed_at": observed_at,
            "age_s": None if observed_at is None else int(max(0, now - observed_at))}


def resolve_profile(rows):
    if not rows:
        return None, []
    rows = sorted(rows, key=lambda r: (r["snapshot_ts"], r.get("version", 1)))
    best, conf = rows[-1], []
    diffs = [k for k in ("sub_status", "dialect", "language") if any(r.get(k) != best.get(k) for r in rows[:-1])]
    if len(rows) > 1 and diffs:
        conf.append("profile_duplicate_versions(" + ",".join(diffs) + "):took_v%s" % best.get("version", 1))
    return best, conf


def resolve_dialect(events, prof, now, cfg):
    """Precedence: app setting (what the viewer will actually see, if recent) > profile > campaign link."""
    cand = {}
    last = [e for e in events if e.get("app_dialect")]
    if last:
        e = last[-1]
        if now - e["event_time"] <= cfg.appsetting_max_age_s:
            cand["app_setting"] = (e["app_dialect"], "event:" + e["event_id"], e["event_time"], e.get("app_lang"))
    if prof:
        cand["profile"] = (prof["dialect"], "profile@" + prof["snapshot_at"], prof["snapshot_ts"], prof.get("language"))
    camp = [e for e in events if e.get("campaign_dialect")]
    if camp:
        e = camp[-1]; cand["campaign_link"] = (e["campaign_dialect"], "event:" + e["event_id"], e["event_time"], None)
    for src in ("app_setting", "profile", "campaign_link"):
        if src in cand:
            used, ref, t, lang = cand[src]
            conf = [f"{k}:{v[0]}" for k, v in cand.items() if v[0] != used]
            three = len({v[0] for v in cand.values()}) >= 3
            return {"used": used, "source": src, "ref": ref, "language": lang, "conflicts": conf,
                    "three_way": three, "observed_at": t, "age_s": int(max(0, now - t))}
    return None


def build_facts(vid, events, prof_rows, now, cfg, catalogue):
    F, conflicts = {}, []
    prof, pc = resolve_profile(prof_rows)
    conflicts += pc
    psnap = prof["snapshot_ts"] if prof else None
    ev = [e for e in events if e["event_time"] <= now]
    opens = [e for e in ev if e["type"] == "app_open"]
    plays = [e for e in ev if e["type"] == "play"]
    buys = [e for e in ev if e["type"] == "purchase"]
    sign = [e for e in ev if e["type"] == "signup"]
    d = resolve_dialect(ev, prof, now, cfg)
    F["dialect"] = {"value": d["used"] if d else None, "source": d["source"] if d else None, "ref": d["ref"] if d else None,
                    "observed_at": d["observed_at"] if d else None, "age_s": d["age_s"] if d else None, "detail": d}
    if d:
        conflicts += d["conflicts"]
    # subscription status: a purchase newer than the (stale) snapshot beats the snapshot
    status, src, ref = (prof["sub_status"], "profile", "profile@" + prof["snapshot_at"]) if prof else ("unknown", "none", None)
    if buys and (psnap is None or buys[-1]["event_time"] > psnap):
        if status != "subscriber":
            conflicts.append(f"status:profile={status}_but_purchase_event_newer")
        status, src, ref = "subscriber", "purchase_event", "event:" + buys[-1]["event_id"]
    F["sub_status"] = fact(status, src, ref, psnap, now)
    ever_paid = bool(buys) or (prof is not None and prof["sub_status"] in ("subscriber", "churned"))
    F["ever_paid"] = fact(ever_paid, "purchase_event" if buys else "profile", ref)
    # consent: newest of snapshot vs consent_change events; absent => no consent (fail closed)
    cons = dict(prof["consent"]) if prof and prof.get("consent") else {"push": False, "whatsapp": False}
    csrc = "profile" if prof else "none(no_profile_row)"
    for e in ev:
        if e["type"] == "consent_change" and (psnap is None or e["event_time"] > psnap):
            cons[e["channel"]] = bool(e["value"]); csrc = "consent_event:" + e["event_id"]
    F["consent"] = fact(cons, csrc, None, psnap, now)
    # signup
    su = sign[0]["event_time"] if sign else (ts(prof["signup_date"] + "T00:00:00Z") if prof else None)
    F["signup_ts"] = fact(su, "signup_event" if sign else "profile", sign[0]["event_id"] if sign else None, su, now)
    F["n_events"] = fact(len(ev), "events")
    F["n_opens_7d"] = fact(sum(1 for e in opens if now - e["event_time"] <= 7 * 86400), "events")
    F["n_plays_7d"] = fact(sum(1 for e in plays if now - e["event_time"] <= 7 * 86400), "events")
    F["minutes_7d"] = fact(sum(e.get("minutes", 0) for e in plays if now - e["event_time"] <= 7 * 86400), "events")
    F["n_plays"] = fact(len(plays), "events")
    F["n_completed"] = fact(sum(1 for e in plays if e.get("completed")), "events")
    F["last_play_ts"] = fact(plays[-1]["event_time"] if plays else None, "events")
    played = [e["title_id"] for e in plays]
    F["titles_played"] = fact(sorted(set(played)), "events")
    F["completed_titles"] = fact(sorted({e["title_id"] for e in plays if e.get("completed") and
                                         catalogue.get(e["title_id"], {}).get("kind") == "film"}), "events")
    gc = Counter(catalogue[t]["genre"] for t in played if t in catalogue)
    F["genres"] = fact([g for g, _ in gc.most_common(2)], "events")
    kc = Counter(catalogue[t]["kind"] for t in played if t in catalogue)
    F["micro_share"] = fact(kc["micro_drama"] / max(1, sum(kc.values())), "events")
    series = {}
    for e in plays:
        t = catalogue.get(e["title_id"])
        if t and t["kind"] == "series" and now - e["event_time"] <= 10 * 86400:
            s = series.setdefault(e["title_id"], set()); s.add(e.get("episode", 1))
    F["open_series"] = fact({t: max(s) for t, s in series.items() if len(s) < 3 and max(s) < 3}, "events")
    F["last_event_id"] = fact(ev[-1]["event_id"] if ev else None, "events")
    return F, conflicts
