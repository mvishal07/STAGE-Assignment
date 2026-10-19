from dataclasses import dataclass

@dataclass
class Config:
    pack_dir: str = "pack"
    out_dir: str = "sample_run"
    provider: str = "mock"            # mock | anthropic (stub)
    seed: int = 7
    # budgets (per decision)
    max_model_calls: int = 8
    max_tool_calls: int = 20
    latency_budget_s: float = 60.0
    hard_fail_s: float = 300.0
    # freshness / trigger rules
    stale_trigger_s: int = 6 * 3600       # older opens: record only, never act
    presence_s: int = 600                 # viewer assumed still in-app for HOME_PAGE
    profile_max_age_s: int = 36 * 3600
    appsetting_max_age_s: int = 7 * 86400
    # messaging policy
    quiet_start: int = 22
    quiet_end: int = 8
    tz_offset_s: int = 19800              # IST
    max_messages_per_day: int = 1
    home_page_dup_window_s: int = 3 * 3600
    followup_delay_s: int = 3 * 3600
    # serving model (virtual time)
    workers: int = 4
    tool_ms: int = 35
    model_ms: int = 1400
    model_timeout_ms: int = 5000
    shed_wait_s: float = 20.0             # queue wait above this -> skip model calls
    drop_wait_s: float = 45.0             # above this -> safe no-action
    judge_grace_s: int = 6 * 3600
    min_titles_home: int = 3
