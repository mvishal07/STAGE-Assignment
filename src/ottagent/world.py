"""Data access: pack loading, catalogue hygiene (injection quarantine), event store with faults."""
import json, os, re, bisect
from collections import defaultdict
from .util import ts

INJ = re.compile(
    r"(ignore|disregard|override)\W+(all\W+)?(previous|prior|above|the)?\W*(instruction|rule|polic)"
    r"|(send|message|notify)\W+(a\W+)?(whatsapp\W+|push\W+)?(message\W+)?(to\W+)?(every|all)\W+(viewer|user|subscriber)"
    r"|system\W+(prompt|instruction)|you\W+(must|should)\W+(now\W+)?(send|ignore)", re.I)


class StoreUnavailable(Exception):
    pass


def in_window(windows, t):
    return any(a <= t < b for a, b in windows)


class World:
    def __init__(self, catalogue, goals, interventions, profiles, holdout, script):
        self.catalogue = {t["title_id"]: dict(t) for t in catalogue}
        self.security = []
        for t in self.catalogue.values():
            t["from_ts"], t["to_ts"] = ts(t["available_from"]), ts(t["available_to"])
            m = INJ.search(t.get("description", "") or "")
            t["quarantined"] = bool(m)
            if m:   # catalogue text is untrusted DATA; never reaches a model, never an instruction
                self.security.append({"type": "catalogue_injection_quarantined", "title_id": t["title_id"],
                                      "matched": m.group(0)})
        self.by_dialect = defaultdict(list)
        for t in sorted(self.catalogue.values(), key=lambda x: -x["popularity"]):
            self.by_dialect[t["dialect"]].append(t["title_id"])
        self.goals = {g["id"]: dict(g) for g in goals}
        self.interventions = interventions
        self.profiles = defaultdict(list)
        for p in profiles:
            p = dict(p); p["snapshot_ts"] = ts(p["snapshot_at"]); self.profiles[p["viewer_id"]].append(p)
        self.holdout = set(holdout)
        self.script = script
        f = script.get("faults", {})
        self.faults = {k: [(ts(a), ts(b)) for a, b in v] for k, v in f.items()}


def load_pack(d):
    rd = lambda n: json.load(open(os.path.join(d, n)))
    rl = lambda n: [json.loads(l) for l in open(os.path.join(d, n)) if l.strip()]
    w = World(rd("catalogue.json"), rd("goals.json"), rd("interventions.json"), rl("profiles.jsonl"),
              rd("holdout.json"), rd("replay_script.json"))
    return w, rl("events.jsonl"), rl("triggers.jsonl")


class EventStore:
    """Idempotent, late-arrival tolerant per-viewer event log."""

    def __init__(self, faults=None):
        self.by_viewer = defaultdict(list)    # sorted by (event_time, event_id)
        self.keys = defaultdict(list)
        self.seen = set()
        self.faults = faults or {}
        self.dups = 0

    @staticmethod
    def normalise(e):
        e = dict(e)
        dev, rec = ts(e["device_ts"]), ts(e["received_at"])
        e["received_ts"] = rec
        e["clock_ahead"] = dev > rec + 120           # impossible: device clock ahead of server receipt
        e["event_time"] = min(dev, rec)              # never trust a future device timestamp
        e["late_s"] = max(0, rec - e["event_time"])  # offline buffering OR slow clock; unknowable per event
        return e

    def ingest(self, e):
        if e["event_id"] in self.seen:
            self.dups += 1
            return None
        self.seen.add(e["event_id"])
        n = self.normalise(e)
        k = (n["event_time"], n["event_id"])
        i = bisect.bisect(self.keys[n["viewer_id"]], k)
        self.keys[n["viewer_id"]].insert(i, k)
        self.by_viewer[n["viewer_id"]].insert(i, n)
        return n

    def events(self, vid, now):
        if any(a <= now < b for a, b in self.faults.get("store", [])):
            raise StoreUnavailable("event store unavailable")
        return self.by_viewer.get(vid, [])
