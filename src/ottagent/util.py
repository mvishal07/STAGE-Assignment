import datetime as dt, hashlib
UTC = dt.timezone.utc

def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

def iso(t):
    return dt.datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

def local_hour(t, tz):
    return int(((t + tz) % 86400) // 3600)

def local_day(t, tz):
    return int((t + tz) // 86400)

def h01(*parts):
    """Deterministic hash -> float in [0,1)."""
    d = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(d[:8], "big") / 2**64
