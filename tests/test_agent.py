from helpers import *
from ottagent.verifier import verify


def new_viewer(vid="V1", **kw):
    return make([prof(vid, signup="2026-09-05", **kw)])


def test_duplicate_trigger_is_idempotent():
    rt, _ = new_viewer()
    tr = trig("V1", NOW)
    d1 = fire(rt, tr)
    d2 = rt.handle(tr, NOW + 400)                       # same event re-delivered later
    assert d1["action"] == "HOME_PAGE" and d2 is None
    assert len(rt.decisions) == 1 and rt.counters["duplicate_trigger_suppressed"] == 1
    assert len([a for a in rt.actions if a["viewer_id"] == "V1"]) == 1


def test_late_buffered_trigger_never_gets_home_page():
    rt, _ = new_viewer()
    d = fire(rt, trig("V1", NOW - 2 * 86400, late=2 * 86400))     # opened 2 days ago, delivered now
    assert d["action"] == "NO_ACTION" and d["reason"].startswith("stale_trigger")


def test_conflicting_dialect_sources_resolved_visibly():
    rt, w = make([prof("V1", dialect="d1", signup="2026-09-05")])
    feed(rt, "V1", [ev("V1", "signup", NOW - 86400 * 4, campaign_dialect="d2", app_dialect="d3", app_lang="ta")])
    d = fire(rt, trig("V1", NOW, dialect="d3"))
    assert d["dialect"]["used"] == "d3" and d["dialect"]["source"] == "app_setting"
    assert sorted(d["dialect"]["conflicts"]) == ["campaign_link:d2", "profile:d1"]
    assert d["dialect"]["three_way"] and d["action"] == "HOME_PAGE"
    assert all(w.catalogue[t]["dialect"] == "d3" for t in d["content"])
    assert d["confidence"] < .7                                       # conflicts lower confidence


def test_store_outage_fails_closed_and_llm_outage_falls_back():
    rt, _ = make([prof("V1", signup="2026-09-05")], faults={"store": [[iso(NOW - 10), iso(NOW + 60)]]})
    d = fire(rt, trig("V1", NOW))
    assert d["action"] == "NO_ACTION" and "tool_failure" in d["reason"]
    rt2, _ = make([prof("V1", signup="2026-09-05")], faults={"llm": [[iso(NOW - 10), iso(NOW + 600)]]})
    d2 = fire(rt2, trig("V1", NOW))
    assert d2["action"] == "HOME_PAGE" and d2["llm"]["status"] == "failed_fallback_to_rules" and d2["goal"]["id"] == "G01"


def test_corrected_goal_rule_reevaluates_only_affected_plans():
    rt, _ = make([prof("V1", signup="2026-09-05"), prof("V2", signup="2026-06-01", status="subscriber")])
    fire(rt, trig("V1", NOW))                                     # plan G01 for V1
    fire(rt, trig("V2", NOW + 5, dialect="d1"))                   # V2: no eligible goal -> no plan
    rt.on_event(ev("V1", "play", NOW + 600, title_id="d1-T00", episode=1, completed=False, minutes=6), NOW + 603)
    o = [x for x in rt.outcomes if x["viewer_id"] == "V1"]
    assert o and o[-1]["status"] == "succeeded"                   # v1: >=5 minutes counts
    rt.apply_rule_update({"goal_id": "G01", "rule_version": 2, "success": "completed only"}, NOW + 3600)
    o = [x for x in rt.outcomes if x["viewer_id"] == "V1"]
    assert o[-1]["status"] == "failed" and o[-1]["rule_version"] == 2 and len(o) == 2   # revision recorded, history kept
    assert rt.counters["rule_update_plans_reevaluated"] == 1 and rt.counters["rule_update_population_skipped"] >= 1


def test_purchase_two_minutes_after_decision():
    rt, _ = make([prof("V1", signup="2026-07-01", status="trial")])
    feed(rt, "V1", [ev("V1", "app_open", NOW - i * 86400 + 100 - 86400, app_dialect="d1", app_lang="hi") for i in range(1, 4)])
    d = fire(rt, trig("V1", NOW))
    assert d["goal"]["id"] == "G02"
    rt.on_event(ev("V1", "purchase", NOW + 118, plan="monthly", app_dialect="d1"), NOW + 120)
    o = rt.outcomes[-1]
    assert o["status"] == "succeeded" and o["attributable_to_agent"] is False and "5min" in o["attribution_note"]


def test_llm_confident_claim_without_evidence_is_rejected():
    rt, _ = make([prof("V1", signup="2026-09-05", status="free")], hallucinate=["V1"])
    d = fire(rt, trig("V1", NOW))
    assert d["goal"]["id"] == "G01" and d["llm"]["status"] == "claim_rejected"
    assert any("llm_claimed_G02" in c for c in d["conflicts"])


def test_catalogue_injection_is_quarantined():
    rt, w = make([prof("V1", signup="2026-09-05")], inject=True)
    d = fire(rt, trig("V1", NOW))
    assert w.catalogue["d1-T11"]["quarantined"] and "d1-T11" not in d["content"]
    assert rt.security and rt.security[0]["type"] == "catalogue_injection_quarantined"


def test_message_requires_consent_and_defaults_to_nothing():
    rt, _ = make([prof("V1", signup="2026-09-05", push=False, wa=False)])
    d = fire(rt, trig("V1", NOW - 3600, late=3600))               # viewer left: only messages possible
    assert d["action"] == "NO_ACTION" and "consent" in d["reason"]


def test_verifier_catches_what_the_decision_step_got_wrong():
    rt, w = new_viewer()
    d = fire(rt, trig("V1", NOW))
    bad = dict(d, content=["d3-T01", "d3-T02", "d3-T03"])         # wrong-dialect content smuggled in
    res = verify(rt, bad, NOW)
    assert "dialect" in res["failed"]
    bad2 = dict(d, action="WHATSAPP", content=["d1-T01"], template="wa_hi_v2")
    rt.states["V1"]["actions"].append({"is_message": True, "ts": NOW - 60, "action": "PUSH", "plan_id": "x", "content": []})
    assert "frequency_cap" in verify(rt, bad2, NOW)["failed"]     # second message the same local day


def test_quiet_hours_defer_followup_and_holdout_is_suppressed():
    rt, _ = make([prof("V1", signup="2026-09-05")], holdout=["V1"])
    d = fire(rt, trig("V1", NOW))
    assert d["status"] == "HOLDOUT_SUPPRESSED" and not rt.actions and rt.states["V1"]["actions"]
    q = ts("2026-09-10T17:30:00Z")                                # 23:00 IST
    got = []
    rt.schedule = lambda t, kind, p: got.append((t, kind))
    f = rt.handle({"viewer_id": "V1", "trigger_id": "FU1", "event": "followup"}, q)
    assert f["action"] == "NO_ACTION" and "quiet_hours" in f["reason"] and got and got[0][1] == "followup"
