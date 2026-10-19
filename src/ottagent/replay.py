"""Replay engine: single virtual-time timeline of events, triggers, follow-ups, judgements, rule updates."""
import heapq, itertools, json, os, time
from .util import ts, iso
from .world import load_pack
from .agent import Runtime
from .providers import make_provider
from .sim import Simulator


def run(cfg, pack_dir=None, out=None, provider=None, limit=None):
    t_wall = time.time()
    world, hist, triggers = load_pack(pack_dir or cfg.pack_dir)
    truth = json.load(open(os.path.join(pack_dir or cfg.pack_dir, "_sim_truth.json")))
    provider = provider or make_provider(cfg, world)
    rt = Runtime(world, cfg, provider)
    return drive(rt, world, hist, triggers, truth, cfg, out or cfg.out_dir, t_wall, limit)


def drive(rt, world, hist, triggers, truth, cfg, out, t_wall=None, limit=None):
    heap, seq = [], itertools.count()
    push = lambda t, kind, p: heapq.heappush(heap, (t, next(seq), kind, p))
    rt.schedule = push
    RS = ts(world.script["replay_start"])
    for e in hist:
        if ts(e["received_at"]) <= RS: rt.store.ingest(e)
        else: push(ts(e["received_at"]), "event", e)
    tags = {}
    for tr in (triggers[:limit] if limit else triggers):
        push(ts(tr["received_at"]), "trigger", tr)
        if tr.get("scripted"): tags[tr["trigger_id"] + "@" + tr["received_at"]] = tr["scripted"]
    for u in world.script.get("rule_updates", []): push(ts(u["at"]), "rule", u)
    sim = Simulator(truth, world, hist, cfg.seed, cfg.tz_offset_s)
    while heap:
        t, _, kind, p = heapq.heappop(heap)
        if kind == "event": rt.on_event(p, t)
        elif kind == "trigger":
            rt.on_event(p, t)
            D = rt.handle(p, t)
            if D:
                D["trigger"]["tag"] = p.get("scripted")      # harness label for reporting only; never read by the agent
                for e in sim.respond(p, D, t): push(ts(e["received_at"]), "event", e)
        elif kind == "followup":
            D = rt.handle({**p, "event": "followup"}, t)
            if D:
                for e in sim.respond({**p, "event": "followup"}, D, t): push(ts(e["received_at"]), "event", e)
        elif kind == "judge": rt.judge(p["viewer_id"], p["plan_id"], t, "deadline")
        elif kind == "rule": rt.apply_rule_update(p, t)
    rt.flush(out)
    return rt, sim, {"wall_s": round(time.time() - (t_wall or time.time()), 1)}
