"""A scriptable in-memory SurfaceAdapter double for fast, deterministic
ReplayEngine unit tests -- no browser, no demo app. Complements (does not
replace) the real end-to-end integration tests: this is for exercising
ReplayEngine's own branching logic (policy decisions, bounded recovery,
error classification) in cases that would be slow or fragile to reproduce
reliably against a real UI every run.

Each of click/fill/read/wait_for looks up a queue of scripted results
keyed by a canonical string derived from the Target/WaitCondition
involved; an unscripted call always succeeds (click/fill: no-op; read:
empty string; wait_for: returns immediately) so tests only need to script
the specific calls they care about.
"""

from __future__ import annotations

from typing import Any

from cuas.domain import Target
from cuas.surface.adapter import Evidence, Observation, SurfaceAdapter, WaitCondition, WaitConditionKind


def _target_key(target: Target) -> str:
    p = target.primary
    return f"{p.strategy}:{sorted(p.params.items())}:{p.frame}"


def _wait_key(condition: WaitCondition) -> str:
    if condition.kind == WaitConditionKind.TEXT_PRESENT:
        return f"text:{condition.text}"
    assert condition.target is not None
    return f"visible:{_target_key(condition.target)}"


class FakeSurfaceAdapter(SurfaceAdapter):
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.url = "http://fake.invalid/"
        self._click: dict[str, list[Exception | None]] = {}
        self._fill: dict[str, list[Exception | None]] = {}
        self._wait: dict[str, list[Exception | None]] = {}
        self._read: dict[str, list[Exception | str]] = {}
        self._evidence: Evidence | None = None
        self._observations: list[Observation] = []

    def script_click(self, target: Target, *results: Exception | None) -> None:
        self._click[_target_key(target)] = list(results)

    def script_fill(self, target: Target, *results: Exception | None) -> None:
        self._fill[_target_key(target)] = list(results)

    def script_wait(self, condition: WaitCondition, *results: Exception | None) -> None:
        self._wait[_wait_key(condition)] = list(results)

    def script_read(self, target: Target, *results: Exception | str) -> None:
        self._read[_target_key(target)] = list(results)

    def script_evidence(self, evidence: Evidence) -> None:
        """Override what capture_evidence() returns. Unscripted, it
        returns an empty/harmless Evidence -- fine for tests that only
        care *whether* evidence capture happened, not its contents (those
        are covered directly in tests/unit/test_evidence_store.py)."""
        self._evidence = evidence

    def script_observation(self, *observations: Observation) -> None:
        """Queues what observe() returns, in order -- added for
        DiscoveryEngine tests, which (unlike ReplayEngine) call observe()
        repeatedly between actions and need to walk a scripted sequence of
        pages (e.g. search form -> results -> member detail) rather than
        the single implicit `url`/empty-text observation replay tests
        never needed to control. Once the queue is exhausted, observe()
        falls back to its original behavior (the current `self.url` with
        empty visible_text) so a test only scripts the observations it
        actually cares about."""
        self._observations.extend(observations)

    def _pop(self, scripts: dict[str, list], key: str, default: Any) -> Any:
        queue = scripts.get(key)
        if not queue:
            return default
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def navigate(self, url: str) -> None:
        self.calls.append(("navigate", url))
        self.url = url

    async def observe(self) -> Observation:
        self.calls.append(("observe",))
        if self._observations:
            observation = self._observations.pop(0)
            self.url = observation.url
            return observation
        return Observation(url=self.url, visible_text="")

    async def click(self, target: Target, *, timeout_ms: int | None = None) -> None:
        key = _target_key(target)
        self.calls.append(("click", key))
        self._pop(self._click, key, None)

    async def fill(self, target: Target, value: str, *, timeout_ms: int | None = None) -> None:
        key = _target_key(target)
        self.calls.append(("fill", key, value))
        self._pop(self._fill, key, None)

    async def read(self, target: Target) -> str:
        key = _target_key(target)
        self.calls.append(("read", key))
        return self._pop(self._read, key, "")

    async def wait_for(self, condition: WaitCondition) -> None:
        key = _wait_key(condition)
        self.calls.append(("wait_for", key))
        self._pop(self._wait, key, None)

    async def capture_evidence(self) -> Evidence:
        self.calls.append(("capture_evidence",))
        if self._evidence is not None:
            return self._evidence
        return Evidence(url=self.url, screenshot_png=b"", visible_text_excerpt="")
