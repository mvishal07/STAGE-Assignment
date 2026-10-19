"""Goal rules: eligibility, deadline, success signal. Success rules are versioned (G01 is corrected mid-replay)."""
PRIORITY = ["G01", "G04", "G02", "G11", "G03", "G08", "G05", "G06", "G12"]
PLAY_GOALS = {"G01", "G03", "G04", "G06", "G08", "G11", "G12"}
PAID_GOALS = {"G02", "G05", "G07"}


def v(F, k, d=None):
    return F[k]["value"] if k in F and F[k]["value"] is not None else d


def eligible(gid, F, now, goal):
    """-> (ok, reason, params). Never asserts without evidence; underspecified/unmeasurable goals abstain."""
    if goal["status"] != "active":
        return False, goal["status"], {}
    su = v(F, "signup_ts"); st = v(F, "sub_status"); day = 86400
    if gid == "G01":
        if su is None: return False, "signup_unknown", {}
        if (now - su) / day > 14: return False, "signup_older_than_14d", {}
        if v(F, "n_completed", 0) > 0: return False, "already_completed_episode", {}
        return True, "new_viewer_no_completed_episode", {}
    if gid == "G02":
        if st != "trial": return False, f"status_is_{st}", {}
        if v(F, "ever_paid"): return False, "already_paid", {}
        if v(F, "n_plays_7d", 0) < 2 and v(F, "n_opens_7d", 0) < 3: return False, "insufficient_engagement", {}
        return True, "trial_engaged_never_paid", {}
    if gid == "G03":
        if v(F, "n_opens_7d", 0) >= 2 and v(F, "n_plays_7d", 0) == 0: return True, "bouncing_no_plays_7d", {}
        return False, "not_bouncing", {}
    if gid == "G04":
        s = v(F, "open_series", {})
        if s:
            t = sorted(s)[0]; return True, "open_series", {"series": t, "next_episode": s[t] + 1}
        return False, "no_open_series", {}
    if gid == "G05":
        return (True, "profile_churned", {}) if st == "churned" else (False, f"status_is_{st}", {})
    if gid == "G06":
        if st == "subscriber" and v(F, "minutes_7d", 0) < 30 and v(F, "n_opens_7d", 0) >= 2: return True, "low_watch_time", {}
        return False, "not_low_watch_subscriber", {}
    if gid == "G08":
        if v(F, "n_plays", 0) >= 2 and v(F, "micro_share", 0) >= .5: return True, "micro_drama_affinity", {}
        return False, "no_micro_affinity", {}
    if gid == "G11":
        tp = v(F, "titles_played", [])
        if st == "trial" and len(tp) == 1: return True, "trial_single_title", {"first_title": tp[0]}
        return False, "not_single_title_trial", {}
    if gid == "G12":
        lp = v(F, "last_play_ts")
        if st == "subscriber" and lp is not None and now - lp > 7 * day: return True, "subscriber_lapsed_watch", {}
        return False, "not_lapsed_subscriber", {}
    return False, "no_rule", {}


def success(gid, rv, events, plan, catalogue):
    """events: de-duplicated, already inside [start, deadline]. -> (ok, signal_event_id)."""
    plays = [e for e in events if e["type"] == "play"]
    if gid == "G01":
        for e in plays:
            if e.get("completed") or (rv < 2 and e.get("minutes", 0) >= 5): return True, e["event_id"]
    elif gid in ("G02", "G05", "G07"):
        for e in events:
            if e["type"] == "purchase": return True, e["event_id"]
    elif gid in ("G03", "G12"):
        if plays: return True, plays[0]["event_id"]
    elif gid == "G04":
        for e in plays:
            if e["title_id"] == plan["params"].get("series") and e.get("episode", 1) >= 3 and e.get("completed"):
                return True, e["event_id"]
    elif gid == "G06":
        m = 0
        for e in plays:
            m += e.get("minutes", 0)
            if m >= 60: return True, e["event_id"]
    elif gid == "G08":
        n = 0
        for e in plays:
            if catalogue.get(e["title_id"], {}).get("kind") == "micro_drama":
                n += 1
                if n >= 3: return True, e["event_id"]
    elif gid == "G11":
        for e in plays:
            if e["title_id"] != plan["params"].get("first_title"): return True, e["event_id"]
    return False, None
