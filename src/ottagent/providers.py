"""Model provider abstraction. Evaluators run with the mock; no API key or data upload needed."""
import json, os
from .world import in_window


class ModelFailure(Exception):
    pass


class MockProvider:
    """Deterministic stand-in. Can be told to hallucinate eligibility for chosen viewers (surprise test)."""

    def __init__(self, hallucinate=(), faults=None):
        self.hallucinate, self.faults = set(hallucinate), faults or {}

    def suggest_goal(self, vid, summary, eligible, now):
        if in_window(self.faults.get("llm", []), now):
            raise ModelFailure("mock model outage")
        if vid in self.hallucinate:          # confident claim with no supporting event
            return {"goal": "G02", "rationale": "Viewer is clearly ready to pay and is eligible for the paid-plan goal."}
        g = eligible[0]
        return {"goal": g, "rationale": f"Rules rank {g} first for this viewer; evidence listed in the decision."}


class AnthropicProvider:
    """Real-model adapter (optional). Sends ONLY the compact fact summary, never raw events or catalogue text.
    Use a zero-data-retention account; the pack is personal data. Not exercised in tests."""

    def __init__(self, model=None, faults=None):
        import anthropic  # lazy import
        self.c, self.model, self.faults = anthropic.Anthropic(), model or os.environ.get("OTT_MODEL", "claude-sonnet-4-5"), faults or {}

    def suggest_goal(self, vid, summary, eligible, now):
        if in_window(self.faults.get("llm", []), now):
            raise ModelFailure("model outage")
        prompt = ("Pick ONE goal id from the list for this viewer. Reply as JSON {\"goal\":..., \"rationale\":...}.\n"
                  f"Allowed goals: {eligible}\nFacts: {json.dumps(summary)}")
        try:
            r = self.c.messages.create(model=self.model, max_tokens=200, timeout=4,
                                       messages=[{"role": "user", "content": prompt}])
            return json.loads(r.content[0].text)
        except Exception as ex:
            raise ModelFailure(str(ex))


def make_provider(cfg, world):
    if cfg.provider == "anthropic":
        return AnthropicProvider(faults=world.faults)
    return MockProvider(world.script.get("llm_hallucination_viewers", []), world.faults)
