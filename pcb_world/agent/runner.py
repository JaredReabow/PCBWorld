"""Resumable end-to-end routing agent on top of the accepted safety API.

The runner is deliberately deterministic-first:

1. enumerate outstanding same-net connections from the engine's ratsnest,
2. generate a bounded set of deterministic candidate plans,
3. apply them in order as atomic transactions, stopping at the first that closes
   the connection and passes the full native acceptance gate (a candidate that
   closes nothing is rolled back and costs no DRC),
4. keep that plan through the accepted transactional API,
5. only when every deterministic candidate has been tried for a connection may a
   planner (scripted or model) propose a plan — and that proposal goes through the
   same validated JSON tool surface as anything else.

Every attempt is recorded, the best verified board is checkpointed, and the run
state is written atomically after each attempt so a stopped run can resume with
provenance verification and without retrying plans that already failed identically.

Native calls run in an owned IPC child with monotonic per-operation deadlines
clamped to the remaining run budget. Verifier process groups are isolated and
reaped on timeout. The runner also bounds attempts, model requests, and tokens.
"""

from __future__ import annotations

import json
import math
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from pcb_world.agent import zone_coverage
from pcb_world.agent.actions import REASON_DRC_REGRESSION
from pcb_world.agent.drc_gate import diff_sets, take_violations
from pcb_world.agent.reference_baseline import capture_envelope
from pcb_world.agent.artifacts import ArtifactStore, ExperimentalArtifactStore
from pcb_world.agent.cli_gate import (
    CliGateConfig,
    not_configured_verdict,
    run_gate,
)
from pcb_world.agent.observations import (
    LayerResolver,
    NetPair,
    compact_observation,
    edge_key,
    enumerate_net_pairs,
    nearest_obstacles,
    progress_summary,
    component_waypoints,
    anchor_diagnosis,
    reanchor_pair,
    render_observation,
    scan_net_pairs,
)
from pcb_world.agent.scheduler import (
    AttemptHistory,
    AttemptRecord,
    Candidate,
    ProgressTracker,
    RunState,
    attempt_plan_key,
    build_provenance,
    compact_exception_text,
    compact_rollback_detail,
    generate_candidates,
    offered_edge_key,
    rank_candidates,
    sha256_file,
)
from pcb_world.agent.rules import RuleContext, engine_rule_status, resolve_rule_context
from pcb_world.agent.session import AgentSession
from pcb_world.agent.tool_api import handle_request, tool_schemas


# ---------------------------------------------------------------------------
# Planner contract and errors
# ---------------------------------------------------------------------------

CATEGORY_TIMEOUT = "timeout"
CATEGORY_RATE_LIMITED = "rate_limited"
CATEGORY_SERVER_ERROR = "server_error"
CATEGORY_BAD_RESPONSE = "bad_response"
CATEGORY_AUTH = "auth"
CATEGORY_CANCELLED = "cancelled"
CATEGORY_UNAVAILABLE = "unavailable"

#: How far around a hole-class refusal the runner looks for the drilled items the
#: finding is about before deriving via seeds from them. A hole-clearance finding
#: names a place where holes are close; the items that made it are its
#: neighbourhood, not the whole board, and the family stays bounded by this.
HOLE_SEED_RADIUS_MM = 2.0


def runner_terminate_owned_process_tree(proc: subprocess.Popen) -> None:
    """Terminate only the isolated process group created for ``proc``."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        pass
    # The direct child can exit while a native-server grandchild keeps the
    # captured pipes open. Signal the whole isolated group even in that case.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if proc.poll() is None:
        proc.wait()


def runner_run_owned_process(
    command: Sequence[str], *, timeout_s: float
) -> subprocess.CompletedProcess[str]:
    """Run a task-owned process tree under one bound and reap it on failure.

    A dedicated session makes the process group exclusive to this child tree;
    timeout or cancellation never signals the runner, GUI, or other processes.
    """
    timeout_s = max(0.0, float(timeout_s))
    if timeout_s <= 0:
        raise subprocess.TimeoutExpired(command, timeout_s)
    proc = subprocess.Popen(
        list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except BaseException:
        runner_terminate_owned_process_tree(proc)
        raise
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


#: Refusals that no other candidate geometry can fix: the engine cannot identify
#: the endpoint at all (its cluster holds no pad or via, so the net is unknown), or
#: the request itself was malformed. Retrying these per candidate only spends
#: iterations, so the runner records the refusal and moves to the next pair.
STRUCTURAL_REFUSALS = frozenset({
    "endpoint_unknown", "endpoint_ambiguous", "unknown_action",
    "malformed_coordinate", "wrong_phase", "unsupported_schema_version",
})

#: How much room an engine lease keeps over the worst work this run has measured.
#: A lease exists so a transaction that turns out bigger than its history cannot
#: be truncated; twice the worst sample is the smallest margin that still says
#: "one more of these, and a bit".
LEASE_MEASURED_FACTOR = 2.0


class PlannerError(RuntimeError):
    """A planner call failed, classified so the runner can react without prose."""

    def __init__(
        self, message: str, *, category: str, retryable: bool = False,
        status: int | None = None, **detail: Any,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.status = status
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "message": str(self),
            "retryable": self.retryable,
            "status": self.status,
            "usage": dict(self.detail.get("usage", {})),
            **{k: v for k, v in self.detail.items() if k != "api_key"},
        }


@dataclass(frozen=True)
class PlannerReply:
    """One planner proposal plus its accounting."""

    request: Mapping[str, Any]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    model: str = ""
    raw: str = ""


class Planner(Protocol):
    """Proposes one tool request for a blocked connection."""

    name: str

    def propose(
        self,
        *,
        observation: Mapping[str, Any],
        tool_schema: Mapping[str, Any],
        previous_attempts: Sequence[Mapping[str, Any]],
    ) -> PlannerReply:  # pragma: no cover - protocol
        ...


class ScriptedPlanner:
    """Deterministic planner for tests and ``--provider scripted``.

    It consumes a queue of pre-baked requests (so a test can drive exact
    scenarios) and otherwise proposes a plain ``connect_targets`` for the pair
    with the connection's own layer, which is the honest "do the obvious thing"
    fallback when a model is not configured.
    """

    name = "scripted"

    def __init__(self, requests: Iterable[Mapping[str, Any]] = ()) -> None:
        self._queue: list[Mapping[str, Any]] = [dict(r) for r in requests]
        self.calls: list[dict[str, Any]] = []

    def propose(
        self,
        *,
        observation: Mapping[str, Any],
        tool_schema: Mapping[str, Any],
        previous_attempts: Sequence[Mapping[str, Any]],
    ) -> PlannerReply:
        del tool_schema
        self.calls.append({
            "connection": observation.get("connection", {}),
            "previous_attempts": len(previous_attempts),
        })
        if self._queue:
            return PlannerReply(request=dict(self._queue.pop(0)), model="scripted")
        connection = dict(observation.get("connection", {}))
        request = {
            "tool": "connect_targets",
            "token": "<token>",                       # replaced by the runner
            "start": list(connection.get("start", [])),
            "target": list(connection.get("target", [])),
            "mode": "walkaround",
        }
        return PlannerReply(request=request, model="scripted")


# ---------------------------------------------------------------------------
# OpenAI-compatible planner (DeepSeek and friends), stdlib HTTP only
# ---------------------------------------------------------------------------

_DEFAULT_KEY_FILES = (
    os.path.expanduser("~/.config/pcbworld/helix-v3-task.key"),
)


def resolve_api_key(
    *,
    key_file: str | None = None,
    env_vars: Sequence[str] = ("DEEPSEEK_API_KEY",),
) -> tuple[str | None, str]:
    """Resolve the API key without ever returning or logging its value.

    Returns ``(key, source)`` where ``source`` is a human-readable *location*
    ("file:/path", "env:NAME") — never the secret itself.
    """
    candidates = [key_file] if key_file else list(_DEFAULT_KEY_FILES)
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    value = handle.read().strip()
            except OSError:
                continue
            if value:
                return value, f"file:{path}"
    for name in env_vars:
        value = os.environ.get(name)
        if value:
            return value, f"env:{name}"
    return None, ""


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect that would send the bearer token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        old_host = urllib.parse.urlsplit(req.full_url).netloc.lower()
        new_host = urllib.parse.urlsplit(newurl).netloc.lower()
        if new_host != old_host:
            raise PlannerError(
                f"refusing a cross-host redirect ({old_host} -> {new_host})",
                category=CATEGORY_AUTH,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class OpenAICompatiblePlanner:
    """Chat-completions planner over an OpenAI-shaped endpoint.

    DeepSeek is OpenAI-compatible, so this one client covers the authorized
    task-local key without touching any global provider configuration. Safety
    properties: https by default, an explicit host allow-list, no cross-host
    redirects, bounded retries by category, and usage accounting from the
    response. The key is read from a file/env at call time and never logged,
    echoed into a prompt, or written into the run state.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_file: str | None = None,
        api_key_env: Sequence[str] = ("DEEPSEEK_API_KEY",),
        timeout_s: float = 60.0,
        max_retries: int = 3,
        temperature: float = 0.0,
        max_tokens: int = 700,
        allow_host: str | None = None,
        chat_path: str = "/chat/completions",
        opener: Any | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https":
            raise PlannerError(
                f"planner base_url must be https, got {parsed.scheme!r}",
                category=CATEGORY_UNAVAILABLE,
            )
        if "@" in parsed.netloc:
            raise PlannerError(
                "planner base_url must not embed credentials",
                category=CATEGORY_UNAVAILABLE,
            )
        if parsed.query or parsed.fragment:
            raise PlannerError(
                "planner base_url must not carry a query or fragment",
                category=CATEGORY_UNAVAILABLE,
            )
        host = parsed.netloc.lower()
        allowed = {"api.deepseek.com"}
        if allow_host:
            allowed.add(allow_host.lower())
        if host not in allowed:
            raise PlannerError(
                f"planner host {host!r} is not allowed (allowed: {sorted(allowed)}); "
                "pass --allow-host to authorise another OpenAI-compatible endpoint",
                category=CATEGORY_UNAVAILABLE,
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_file = api_key_file
        self.api_key_env = tuple(api_key_env)
        self.timeout_s = float(timeout_s)
        self.max_retries = max(1, int(max_retries))
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.chat_path = chat_path if chat_path.startswith("/") else f"/{chat_path}"
        self.name = f"openai-compatible:{self.model}"
        self.calls: list[dict[str, Any]] = []
        self.request_count = 0            # HTTP attempts (retries included)
        # Budget enforced *inside* the retry loop: checking only before the call
        # let one call spend three requests, and a run's request ceiling is a
        # spend limit, not a suggestion. ``None`` means "no opinion".
        self.requests_remaining: int | None = None
        self.tokens_remaining: int | None = None
        self.deadline: float | None = None
        self.on_request_reserved: Callable[[], None] | None = None
        self._clock: Callable[[], float] = time.monotonic
        self.usage_totals: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
            "cached_tokens": 0,
        }
        self.escalations = 0
        self._opener = opener or urllib.request.build_opener(_SameHostRedirectHandler)

    def set_budget(
        self,
        *,
        requests_remaining: int | None = None,
        tokens_remaining: int | None = None,
        deadline: float | None = None,
        clock: Callable[[], float] | None = None,
        on_request_reserved: Callable[[], None] | None = None,
    ) -> None:
        """Set the budget this instance may spend across its next ``propose``.

        ``deadline`` is a duration in seconds from now (not an absolute clock
        reading), so the caller does not have to share this object's clock.
        """
        self.requests_remaining = requests_remaining
        self.tokens_remaining = tokens_remaining
        self._clock = clock or time.monotonic
        self.deadline = (
            None if deadline is None else self._clock() + float(deadline)
        )
        self.on_request_reserved = on_request_reserved

    def _budget_error(self) -> PlannerError | None:
        """The reason no further HTTP attempt may be made, or ``None``."""
        usage = dict(self.usage_totals)
        if self.requests_remaining is not None and self.requests_remaining <= 0:
            return PlannerError(
                "planner request budget exhausted before the retry completed",
                category=CATEGORY_CANCELLED, retryable=False, usage=usage,
            )
        if self.deadline is not None and self._clock() >= self.deadline:
            return PlannerError(
                "planner time budget exhausted before the retry completed",
                category=CATEGORY_CANCELLED, retryable=False, usage=usage,
            )
        if self.tokens_remaining is not None:
            spent = (self.usage_totals["prompt_tokens"]
                     + self.usage_totals["completion_tokens"])
            if spent >= self.tokens_remaining:
                return PlannerError(
                    "planner token budget exhausted before the retry completed",
                    category=CATEGORY_CANCELLED, retryable=False, usage=usage,
                )
        return None

    def _note_usage(self, usage: Mapping[str, Any], details: Mapping[str, Any],
                    prompt_details: Mapping[str, Any]) -> None:
        self.usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        self.usage_totals["completion_tokens"] += int(
            usage.get("completion_tokens", 0) or 0)
        self.usage_totals["reasoning_tokens"] += int(
            details.get("reasoning_tokens", 0) or 0)
        self.usage_totals["cached_tokens"] += int(
            (prompt_details.get("cached_tokens",
                                usage.get("prompt_cache_hit_tokens", 0)) or 0)
        )

    # -- prompt ------------------------------------------------------------

    @staticmethod
    def build_prompt(
        observation: Mapping[str, Any], tool_schema: Mapping[str, Any]
    ) -> tuple[str, str]:
        system = (
            "You are a routing planner that outputs ONE JSON object per turn. "
            "Do not answer in prose. Do not restate these instructions. Do not "
            "explain. Your entire reply must be a single JSON object and nothing "
            "else, starting with '{' and ending with '}'. Coordinates are "
            "millimetres, layers are human 1..N, modes are mark_obstacles|shove|"
            "walkaround. Add a waypoint only when the direct route is blocked. "
            "Never invent fields; an invalid request is refused with a reason."
        )
        user = (
            'Example reply (shape only): {"tool":"connect_targets",'
            '"start":[10,10,1],"target":[40,10,1],"mode":"walkaround"}\n\n'
            "Tool schema:\n"
            + json.dumps(tool_schema, sort_keys=True, separators=(",", ":"))
            + "\n\nConnection observation:\n"
            + render_observation(observation)
            + "\n\nNow output the single JSON object that plans this connection."
        )
        return system, user

    @staticmethod
    def parse_reply(text: str) -> Mapping[str, Any]:
        """Extract one JSON object from a model reply.

        Tolerant of the shapes models actually produce: fenced JSON, narration
        before or after the object, and several objects in one reply (the first
        one carrying a ``tool`` field wins). Parsing is attempted at each ``{``
        with a raw decoder, so trailing text is not an error.
        """
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`")
            if candidate.lower().startswith("json"):
                candidate = candidate[4:]
        decoder = json.JSONDecoder()
        first_error: str | None = None
        index = candidate.find("{")
        while index != -1:
            try:
                payload, _end = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError as exc:
                first_error = first_error or f"{exc}"
                index = candidate.find("{", index + 1)
                continue
            if isinstance(payload, Mapping) and "tool" in payload:
                return payload
            index = candidate.find("{", index + 1)
        if first_error is None:
            raise PlannerError(
                f"planner reply contained no JSON object: {text[:200]!r}",
                category=CATEGORY_BAD_RESPONSE, retryable=True,
            )
        raise PlannerError(
            f"planner reply was not a usable JSON tool request ({first_error}): "
            f"{text[:200]!r}",
            category=CATEGORY_BAD_RESPONSE, retryable=True,
        )

    # -- call --------------------------------------------------------------

    def propose(
        self,
        *,
        observation: Mapping[str, Any],
        tool_schema: Mapping[str, Any],
        previous_attempts: Sequence[Mapping[str, Any]],
    ) -> PlannerReply:
        key, source = resolve_api_key(
            key_file=self.api_key_file, env_vars=self.api_key_env
        )
        if not key:
            raise PlannerError(
                "no API key available: pass --api-key-file or set the configured "
                "environment variable (no value is logged)",
                category=CATEGORY_AUTH,
            )
        payload_observation = dict(observation)
        if previous_attempts:
            payload_observation["attempts"] = list(previous_attempts)
        system, user = self.build_prompt(payload_observation, tool_schema)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": min(self.max_tokens, self.tokens_remaining)
            if self.tokens_remaining is not None else self.max_tokens,
            "stream": False,
        }
        url = f"{self.base_url}{self.chat_path}"
        last_error: PlannerError | None = None
        self.usage_totals = {
            "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
            "cached_tokens": 0,
        }
        request_count_at_start = self.request_count
        for attempt in range(self.max_retries):
            budget_error = self._budget_error()
            if budget_error is not None:
                raise budget_error
            request = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
                method="POST",
            )
            started = time.time()
            self.request_count += 1
            if self.requests_remaining is not None:
                self.requests_remaining -= 1
            if self.on_request_reserved is not None:
                self.on_request_reserved()
            try:
                remaining = (
                    max(0.001, self.deadline - self._clock())
                    if self.deadline is not None else self.timeout_s
                )
                with self._opener.open(
                    request, timeout=min(self.timeout_s, remaining)
                ) as response:
                    raw = response.read().decode("utf-8", "replace")
                    status = int(getattr(response, "status", 200))
                parsed = json.loads(raw)
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                if status == 401 or status == 403:
                    last_error = PlannerError(
                        "planner authentication failed", category=CATEGORY_AUTH,
                        status=status,
                    )
                    break
                if status == 429:
                    last_error = PlannerError(
                        "planner rate limited", category=CATEGORY_RATE_LIMITED,
                        status=status, retryable=True,
                    )
                elif status >= 500:
                    last_error = PlannerError(
                        "planner server error", category=CATEGORY_SERVER_ERROR,
                        status=status, retryable=True,
                    )
                else:
                    last_error = PlannerError(
                        f"planner request rejected (HTTP {status})",
                        category=CATEGORY_BAD_RESPONSE, status=status,
                    )
                    break
            except TimeoutError as exc:
                last_error = PlannerError(
                    f"planner timed out after {self.timeout_s:.0f}s: {exc}",
                    category=CATEGORY_TIMEOUT, retryable=True,
                )
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                is_timeout = "timed out" in str(reason).lower()
                last_error = PlannerError(
                    f"planner connection error: {reason}",
                    category=CATEGORY_TIMEOUT if is_timeout else CATEGORY_UNAVAILABLE,
                    retryable=True,
                )
            except PlannerError:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport surprise is classified
                last_error = PlannerError(
                    f"planner transport error: {type(exc).__name__}: {exc}",
                    category=CATEGORY_UNAVAILABLE,
                )
                break
            else:
                usage = parsed.get("usage", {}) if isinstance(parsed, Mapping) else {}
                details = usage.get("completion_tokens_details", {}) or {}
                prompt_details = usage.get("prompt_tokens_details", {}) or {}
                # Every HTTP attempt's tokens are recorded, including attempts
                # whose reply is unusable: the spend happened either way.
                self._note_usage(usage, details, prompt_details)
                try:
                    choice = parsed["choices"][0]
                    message = choice["message"]
                except (KeyError, IndexError, TypeError):
                    last_error = PlannerError(
                        "planner response had no choices[0].message.content",
                        category=CATEGORY_BAD_RESPONSE, retryable=True,
                    )
                    break
                # Only `content` is an answer. `reasoning_content` is the model
                # thinking out loud: executing it would be running the model's
                # scratchpad as a plan.
                content = message.get("content") or ""
                if not str(content).strip():
                    finish = str(choice.get("finish_reason", ""))
                    if finish == "length" and self.escalations < 1:
                        # A reasoning model spent the whole allowance thinking.
                        # Retrying the *same* under-budget request wastes a
                        # request; escalate the output budget once instead.
                        self.escalations += 1
                        self.max_tokens = min(int(self.max_tokens * 2), 32_000)
                        body["max_tokens"] = self.max_tokens
                        if last_error is None:
                            last_error = PlannerError(
                                "planner hit max_tokens before emitting JSON; "
                                f"escalating the output budget to {self.max_tokens}",
                                category=CATEGORY_BAD_RESPONSE, retryable=True,
                            )
                        continue
                    last_error = PlannerError(
                        "planner returned no content"
                        + (" (hit max_tokens before emitting JSON; raise "
                           "--max-new-tokens)" if finish == "length" else ""),
                        category=CATEGORY_BAD_RESPONSE,
                        retryable=finish != "length",
                        usage={
                            "prompt_tokens": self.usage_totals["prompt_tokens"],
                            "completion_tokens": self.usage_totals["completion_tokens"],
                            "reasoning_tokens": self.usage_totals["reasoning_tokens"],
                            "cached_tokens": self.usage_totals["cached_tokens"],
                        },
                    )
                    break
                try:
                    request_payload = self.parse_reply(str(content))
                except PlannerError as exc:
                    # A malformed reply is often transient (truncation, drift);
                    # retry it a bounded number of times, then give up loudly.
                    exc.detail.setdefault("usage", {
                        "prompt_tokens": self.usage_totals["prompt_tokens"],
                        "completion_tokens": self.usage_totals["completion_tokens"],
                        "reasoning_tokens": self.usage_totals["reasoning_tokens"],
                        "cached_tokens": self.usage_totals["cached_tokens"],
                    })
                    if not exc.retryable or attempt + 1 >= self.max_retries:
                        raise
                    last_error = exc
                    delay = min(8.0, 2.0 ** attempt)
                    if self.deadline is not None:
                        delay = min(delay, max(0.0, self.deadline - self._clock()))
                    if delay:
                        time.sleep(delay)
                    continue
                totals = self.usage_totals
                if self.tokens_remaining is not None:
                    self.tokens_remaining = max(
                        0, self.tokens_remaining
                        - int(totals["prompt_tokens"]) - int(totals["completion_tokens"])
                    )
                reply = PlannerReply(
                    request=request_payload,
                    prompt_tokens=int(totals["prompt_tokens"]),
                    completion_tokens=int(totals["completion_tokens"]),
                    reasoning_tokens=int(totals["reasoning_tokens"]),
                    cached_tokens=int(totals["cached_tokens"]),
                    model=str(parsed.get("model", self.model)),
                    raw=str(content),
                )
                self.calls.append({
                    "model": reply.model,
                    "status": status,
                    "duration_s": round(time.time() - started, 3),
                    "prompt_tokens": reply.prompt_tokens,
                    "completion_tokens": reply.completion_tokens,
                    "reasoning_tokens": reply.reasoning_tokens,
                    "cached_tokens": reply.cached_tokens,
                    "http_attempts": self.request_count,
                    "escalations": self.escalations,
                    "key_source": source,
                })
                return reply
            if last_error is not None and last_error.retryable and attempt + 1 < self.max_retries:
                delay = min(8.0, 2.0 ** attempt)
                if self.deadline is not None:
                    delay = min(delay, max(0.0, self.deadline - self._clock()))
                if delay:
                    time.sleep(delay)
                continue
            break
        error = last_error or PlannerError(
            "planner produced no usable reply", category=CATEGORY_UNAVAILABLE
        )
        error.detail.setdefault("usage", dict(self.usage_totals))
        error.detail.setdefault("usage_known", error.category not in {
            CATEGORY_TIMEOUT, CATEGORY_RATE_LIMITED, CATEGORY_SERVER_ERROR,
            CATEGORY_UNAVAILABLE,
        })
        error.detail.setdefault(
            "usage_unknown_requests",
            (self.request_count - request_count_at_start)
            if not error.detail["usage_known"] else 0,
        )
        raise error


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedInputs:
    """The board plus the project/rules files a run is actually validated against.

    A run is only meaningful if the rules it was accepted under travel with the
    artifact, so the sidecars are resolved once (explicit argument, else the
    board's own ``<stem>.kicad_pro`` / ``<stem>.kicad_dru``), hashed into the
    provenance, and copied next to the saved board. Resolving them only when the
    caller happened to pass ``--project``/``--rules`` was how a run ended up with
    ``None`` hashes and no sidecars in its artifact.
    """

    board_path: str
    project_path: str | None
    rules_path: str | None
    notes: tuple[str, ...] = ()

    def rule_context(self, board_path: str | None = None) -> RuleContext:
        return RuleContext(
            board_path=os.path.abspath(board_path or self.board_path),
            rules_path=self.rules_path or "",
            project_path=self.project_path or "",
            source="runner:resolved-inputs",
        )

    def to_evidence(self) -> dict[str, Any]:
        return {
            "board_path": self.board_path,
            "project_path": self.project_path,
            "rules_path": self.rules_path,
            "notes": list(self.notes),
        }


def resolve_inputs(
    board_path: str, *, project_path: str | None = None, rules_path: str | None = None
) -> ResolvedInputs:
    """Resolve the sidecars a run will be validated against (missing files -> None)."""
    context = resolve_rule_context(
        board_path, project_path=project_path, rules_path=rules_path
    )
    notes: list[str] = []

    def keep(path: str, label: str) -> str | None:
        if not path:
            return None
        if os.path.isfile(path):
            return os.path.abspath(path)
        notes.append(f"{label} not found and therefore not hashed or copied: {path}")
        return None

    return ResolvedInputs(
        board_path=os.path.abspath(board_path),
        project_path=keep(context.project_path, "project"),
        rules_path=keep(context.rules_path, "rules"),
        notes=tuple(notes),
    )


@dataclass
class RunnerConfig:
    """Limits and wiring for one run (all optional except the board)."""

    board_path: str
    run_dir: str
    project_path: str | None = None
    rules_path: str | None = None
    max_attempts: int = 40
    max_model_requests: int = 0          # 0 = deterministic only
    max_total_tokens: int = 0            # 0 = unlimited (within requests)
    time_limit_s: float = 900.0
    #: Hard deadline for each call into the task-owned KiCad engine child.
    engine_call_timeout_s: float = 300.0
    #: Floor for the wall-clock one engine lease buys, *independent of the run's
    #: soft scheduling deadline*. A lease is held while an iteration is touching
    #: the engine — its reads, its scan, and the atomic copper transaction they
    #: lead to, whose steps, acceptance DRC and rollback are the calls whose
    #: reaping costs the session its proof. The child reaped at that deadline is
    #: the child holding the transaction's checkpoint, so those calls are bounded
    #: by ``engine_call_timeout_s`` per call and by this lease as a whole, and the
    #: run loses no session to the clock. Each lease *window* is individually
    #: bounded by the value below and the run can hold two windows past its soft
    #: deadline — the one that crossed it, plus the closing verification's — so
    #: the overrun is bounded by two windows, not one (see ``_transaction_lease``
    #: and ``metrics["engine_leases"]["max_overrun_s"]``). The window is widened to
    #: ``engine_call_timeout_s`` (one contained call must fit inside it) and to
    #: ``LEASE_MEASURED_FACTOR`` times the worst whole-board DRC or whole attempt
    #: measured so far. 0 disables it and restores the plain soft-deadline clamp,
    #: which is kept for tests and for callers that want a hard wall-clock stop
    #: more than containment.
    transaction_lease_s: float = 300.0
    #: Clock for the lease and for the two wall-clock cost measurements. Injectable
    #: so a test can drive the lease deterministically; the run's own
    #: ``time_limit_s`` is measured on ``clock`` instead, and the two must not be
    #: confused.
    wall_clock: Callable[[], float] = time.monotonic
    max_pairs: int | None = None
    max_per_net: int = 1
    per_net_tries: int = 3
    #: Coverage/fairness. With this on, one scan offers a net's *first pair that
    #: has no attempt on the current board generation* in addition to the capped
    #: representative, and the selector picks the pair with the fewest attempts on
    #: this generation before repeating one. The per-pair budget and the failed-
    #: plan dedup are unchanged: this only decides which pair gets the next
    #: attempt, so an unchanged board reaches its whole outstanding set instead of
    #: spending the same three attempts on the same few pairs.
    coverage_first: bool = True
    #: Nets the operator pinned to the front of every scan. The queue is fair
    #: (round-robin across nets) but a trial brief can name specific geometry it
    #: wants attempted while there is still budget to verify it, instead of
    #: whenever the rotation happens to reach it. Order inside the pinned group
    #: and inside the rest is the scan's own.
    priority_nets: tuple[int, ...] = ()
    #: Exact edges pinned to the front of every scan, each
    #: ``(x0, y0, layer0, x1, y1, layer1)`` in mm and human copper layers. A net
    #: pin is not enough when the work is a *particular* edge: one net can offer
    #: several pairs (same-layer, cross-layer, coincident) and the scan's own
    #: order decides which is attempted, so a net-pinned trial can spend its whole
    #: budget on an edge the phase never meant to study. Matching is
    #: direction-tolerant and rounded to 3 decimals, because the scan's own
    #: anchors may carry float noise and either endpoint may be offered first.
    #: Edge pins outrank net pins; nothing here changes which pairs exist.
    priority_edges: tuple[tuple[float, float, int, float, float, int], ...] = ()
    candidate_limit: int = 6
    stall_patience: int = 6
    #: Verified pour/component waypoints offered per pair, and the interior
    #: samples spent proving them (0 disables the strategy).
    pour_waypoints: int = 3
    pour_samples: int = 12
    #: Alternatives aimed at the violations earlier plans of the same pair were
    #: refused by (see ``generate_candidates(drc_hints=...)``); 0 disables them.
    drc_candidate_limit: int = 2
    #: The clearance the violation-centred search steps by, and how many outward
    #: waypoints one pair may spend in total. These are the two knobs the
    #: refusal-driven families are budgeted by, so raising them has to change both
    #: *generation* and *selection* - a pair whose every configured refusal plan
    #: is already tried must stop outranking one that still has plans to offer.
    drc_clearance_mm: float = 0.4
    drc_clearance_probes: int = 4
    #: Waypoints that clear an observed obstacle's own extent, one shared budget
    #: per pair (``kind="drc_extent"``); 0 disables the family.
    drc_extent_probes: int = 2
    #: Native component membership offers. ``component_offers_per_net`` is the
    #: number of distinct disconnected component pairs one net may contribute to
    #: a scan (0 disables the source), ``components_per_net`` bounds how many of
    #: a net's components are considered, and ``substitution_variants`` is how
    #: many proved substitutes an edge whose anchor carries no copper may be
    #: offered as (0 disables substitution).
    component_offers_per_net: int = 4
    components_per_net: int = 12
    substitution_variants: int = 3
    #: Free-position via search: a bounded grid around the pair's anchors is
    #: probed with the engine's via prefilter, and the positions it does not
    #: refuse become candidates. The prefilter is a *filter* only - it answers
    #: "not on a through-hole pad" and nothing about tracks, zones, holes or
    #: clearance across the via's span - so the transaction's native DRC remains
    #: the authority. 0 disables the search.
    via_search: bool = True
    via_search_radius_mm: float = 3.0
    via_search_pitch_mm: float = 0.15
    via_search_candidates: int = 5
    via_search_probes: int = 1500
    via_search_deadline_s: float = 8.0
    #: Keep accepted spots at least this far from a violation the gate already
    #: refused this pair for: a hole-clearance finding names a place where holes
    #: are dense, and a prefilter that does not model holes will happily accept
    #: the same neighbourhood again.
    via_search_avoid_mm: float = 0.35
    #: Accept at most one spot per band of this width, so the candidates are not
    #: four points inside one 0.15 mm neighbourhood - if the first band is refused,
    #: the next candidate is somewhere else rather than beside it.
    via_search_band_mm: float = 0.35
    #: How many of the candidate slots are reserved for spots whose far-layer
    #: copper is proved to be the target's own component (the via itself closes
    #: the hop). The search keeps looking for those after the unproved slots are
    #: full, so a nearby unproved spot cannot crowd them out.
    via_search_continuations: int = 2
    #: Hole-derived via seeds: for a refusal whose class names a *hole*, the
    #: candidate sites are placed from the actual drilled geometry near the
    #: refusal (hole centre plus hole radius, the via's own radius and the board's
    #: hole-to-hole rule) instead of only from a grid around the anchors. The
    #: engine's ``pad_block_reason`` stays a prefilter and the native DRC stays the
    #: authority. 0 disables the seeds.
    via_hole_seeds: int = 4
    #: Hole-to-hole clearance the seeds are offset by. ``None`` reads the board's
    #: own ``min_hole_to_hole_mm``; a board rule of 0 means "no constraint", which
    #: is a real answer and is used as-is.
    via_hole_clearance_mm: float | None = None
    #: Zone-copper prefilter (advisory, ranking only). **Opt-in, default off:**
    #: it changes which plans a sweep spends its budget on, and no control run
    #: has yet shown that change to be an improvement, so it stays a deliberate
    #: choice rather than an inherited default. With it on, each candidate's own
    #: waypoints and a bounded sample of its own start->target corridor are
    #: tested against the board's *filled* zone copper, and a plan touching a
    #: foreign net's pour — within the board's own copper radius + clearance —
    #: is moved behind the others.
    #:
    #: It removes nothing from the generated candidate list, but that is not the
    #: same as "every plan still gets tried": the sweep truncates to
    #: ``candidate_limit`` *after* ranking, so a plan moved late can miss this
    #: attempt entirely. The samples are also the plan's named geometry, not the
    #: path the router will lay, so the signal is only ever used to rank;
    #: the transactional DRC remains the only authority on whether copper may be
    #: kept. An engine that cannot answer the query, or a point the query cannot
    #: resolve, ranks exactly as before.
    zone_prefilter: bool = False
    #: Spacing of the corridor samples the prefilter takes along a candidate's
    #: own start→target leg, and the cap on samples per leg. A candidate with no
    #: waypoints (direct / shove) declares no geometry except the line between
    #: its endpoints, so without these its corridor could never be classified.
    #: Both bounds are shared by every leg of every candidate of a pair, so the
    #: prefilter's cost per pair is bounded by
    #: ``candidate_limit * 2 * (zone_prefilter_samples + 1)`` point queries.
    zone_prefilter_pitch_mm: float = 0.5
    zone_prefilter_samples: int = 48
    #: Measured via-placement openings for a pair whose endpoint layers differ:
    #: how many to offer (0 disables the family, and is the default for the same
    #: reason the ranking is opt-in - it is new geometry that changes what a
    #: sweep tries, so a run must ask for it). ``_zone_openings`` samples the
    #: pair's own corridor, measures each sample against the board's *filled*
    #: pour copper on both faces a through via touches, and offers only spots
    #: with at least the board's clearance + via radius of room; the family is
    #: silent when nothing qualifies. The opening is a measurement, never a
    #: legality claim: the transactional native DRC still decides.
    zone_gap_via: int = 0
    #: Corridor sampling for that family, and how far apart (and how far from
    #: observed foreign obstacles) two openings must be.
    zone_gap_via_pitch_mm: float = 0.5
    zone_gap_via_samples: int = 96
    zone_gap_via_band_mm: float = 0.5
    #: How far the opening search looks for foreign pour copper. It is a
    #: *ranking* window, not the acceptance margin: a spot with 3 mm of room is
    #: better than one with 0.3 mm even when both pass.
    zone_gap_via_search_mm: float = 3.0
    #: Refusal-aware ordering for those openings. **Opt-in, default off.**
    #:
    #: With this on, a pair whose *exact* edge has same-generation prior DRC
    #: refusal evidence has its `zone_gap_via` candidates moved ahead of the
    #: direct candidates that the same evidence names as already refused, before
    #: the sweep's `candidate_limit` truncation. The point is evaluation
    #: coverage: a measured opening generated specifically for a refused edge
    #: loses its slot to the two direct plans that have already been refused on
    #: that same copper.
    #:
    #: Two limits keep it from touching anything else. It only fires when the
    #: pair's exact edge key carries refusal evidence *on this board digest* — a
    #: record measured on another generation is not evidence about this copper,
    #: so it is ignored rather than inherited. And it only demotes the direct
    #: candidates that the evidence names, by plan key: a clean connection, a
    #: refusal with no evidence for a particular direct plan, and every other
    #: family keep their generated order.
    zone_gap_via_priority: bool = False
    #: Ask the engine for its scoped native DRC re-check on the *verification*
    #: pass (see :meth:`DrcGate.verify`). Off by default: it changes how the
    #: acceptance delta is measured, so it is enabled only together with the
    #: full-vs-incremental differential evidence in
    #: `tools/reliability/drc_incremental_differential.py`. The saved artifact is
    #: still re-verified by a fresh native child with a whole-board DRC.
    incremental_drc: bool = False
    #: Run time the loop keeps in hand before it starts another pair attempt.
    #: An attempt that closes a connection has to pay for a whole-board
    #: acceptance DRC, and that pass cannot be interrupted and picked up again:
    #: ``_native_timeout_s`` clamps each native call to the run's remaining
    #: budget, so a call dispatched near the limit is reaped mid-pass along with
    #: the engine child that owns the transaction's checkpoint. The rollback then
    #: has no child to restore through, the session quarantines itself, and the
    #: run stops — fail-closed, but on a clock accident rather than on the board.
    #: So the loop refuses to start an attempt it cannot verify. The requirement
    #: is ``max(attempt_headroom_s, attempt_headroom_factor * measured)`` where
    #: ``measured`` is the worst whole-board DRC or whole attempt seen so far
    #: (see ``_attempt_headroom_s``). This floor is only a floor: the requirement
    #: is the larger of it and the measurements, so a board whose DRC is expensive
    #: stops earlier than the floor asks. It is deliberately non-zero — the loop's
    #: own reads and its scan are work too, and a run that enters them with almost
    #: nothing left reaches the engine in a state where only the lease can save it.
    attempt_headroom_s: float = 30.0
    #: Multiplier applied to the measured worst-case attempt cost. Above 1 so the
    #: next attempt is expected to fit with the same margin the last one had.
    attempt_headroom_factor: float = 1.25
    planner: Planner | None = None
    observe_radius_mm: float = 3.0
    obstacle_limit: int = 8
    record_prompts: bool = False
    #: Reopen a promoted artifact in a fresh engine and record what it reads.
    #: Off for callers that pass a custom engine factory (a test double cannot be
    #: reopened) and for speed; the artifact is still staged and hashed.
    verify_artifacts: bool = True
    artifact_verifier: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None
    #: Installed-KiCad-CLI gate. ``None`` means native-only acceptance, which is
    #: recorded as ``not_configured`` - a native pass is never reported as a CLI
    #: acceptance.
    cli_gate: CliGateConfig | None = None
    #: Test seam mirroring ``artifact_verifier``: returns the CLI verdict evidence.
    cli_verifier: Callable[..., Mapping[str, Any]] | None = None
    #: Test seam mirroring ``artifact_verifier`` for the fresh two-board
    #: terminal-partition gate; the injected native verifier may carry the same
    #: evidence under its own ``terminal_partition`` key instead.
    terminal_verifier: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None
    #: Experimental repairs use a separate durable staging pointer and never
    #: alter artifacts/accepted_artifact.json. They require an immutable original.
    experimental_staging: bool = False
    acceptance_reference_board_path: str | None = None
    acceptance_reference_project_path: str | None = None
    acceptance_reference_rules_path: str | None = None
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None
    engine_factory: Callable[[str], Any] | None = None
    session_factory: Callable[[Any, str], AgentSession] | None = None
    clock: Callable[[], float] = time.time


@dataclass
class RunReport:
    """Structured outcome of one run."""

    status: str
    stop_reason: str
    attempts: int = 0
    plan_evaluations: int = 0
    accepted: int = 0
    pairs_before: int = 0
    progress_before: dict[str, Any] = field(default_factory=dict)
    progress_after: dict[str, Any] = field(default_factory=dict)
    drc: dict[str, Any] = field(default_factory=dict)
    best_board_path: str | None = None
    best_board_sha256: str | None = None
    model_usage: dict[str, Any] = field(default_factory=dict)
    mode_usage: dict[str, int] = field(default_factory=dict)
    planner_categories: dict[str, int] = field(default_factory=dict)
    timings: dict[str, Any] = field(default_factory=dict)
    run_state_path: str | None = None
    notes: list[str] = field(default_factory=list)
    #: What a fresh engine read back from the promoted artifact (see
    #: ``_reopen_artifact``); ``{}`` when no artifact was promoted.
    artifact_verification: dict[str, Any] = field(default_factory=dict)

    @property
    def improved(self) -> bool:
        return (
            int(self.progress_after.get("unrouted_edges", 0)),
            int(self.progress_after.get("pad_group_total", 0)),
        ) < (
            int(self.progress_before.get("unrouted_edges", 0)),
            int(self.progress_before.get("pad_group_total", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stop_reason": self.stop_reason,
            "attempts": self.attempts,
            "plan_evaluations": self.plan_evaluations,
            "accepted": self.accepted,
            "improved": self.improved,
            "pairs_before": self.pairs_before,
            "progress_before": self.progress_before,
            "progress_after": self.progress_after,
            "drc": self.drc,
            "best_board_path": self.best_board_path,
            "best_board_sha256": self.best_board_sha256,
            "model_usage": self.model_usage,
            "mode_usage": self.mode_usage,
            "planner_categories": self.planner_categories,
            "timings": self.timings,
            "run_state_path": self.run_state_path,
            "notes": self.notes,
            "artifact_verification": self.artifact_verification,
        }


class RoutingRunner:
    """Deterministic-first, resumable routing agent (see the module docstring)."""

    def __init__(self, config: RunnerConfig, *, state: RunState | None = None) -> None:
        self.config = config
        self.state = state
        self.resolver: LayerResolver | None = None
        self._pending_digest: str | None = None
        self._planner_request_offset: int | None = None
        # Set when the run must stop for a safety reason (unverified state or a
        # quarantine) rather than a budget reason; drained by ``run``.
        self._stop_requested: str = ""
        self.inputs: ResolvedInputs | None = None
        self._run_started: float | None = None
        self._elapsed_prior_s = 0.0
        self._structural_refusal: str = ""
        #: Verified pour/component waypoints per (pair key, board digest): the
        #: native connectivity queries behind them are not free, and the answer
        #: only changes when the copper does.
        self._pour_cache: dict[tuple, list[dict[str, Any]]] = {}
        #: Free via positions per (pair key, board digest), for the same reason.
        self._via_position_cache: dict[tuple, list[dict[str, Any]]] = {}
        #: (engine identity, zone-fill epoch) -> the zone-copper prefilter that
        #: describes it. Zone fill cannot change under routing, so this survives
        #: the run and is replaced only by a reload or a refill.
        self._zone_coverage_cache: tuple[tuple, Any] | None = None
        #: The board's own copper-radius-plus-clearance margin, read once.
        self._zone_margin_cache: tuple[float, str] | None = None
        #: Measured via openings per (pair key, board digest, family size).
        self._zone_opening_cache: dict[tuple, list[dict[str, Any]]] = {}
        #: the source board's DRC multiset, captured under the collision-aware
        #: policy; the saved-artifact gate replays it in a fresh process
        self._acceptance_baseline_evidence: dict[str, Any] | None = None
        self._acceptance_reference_terminals: dict[str, Any] | None = None
        self._active_artifact_store: ArtifactStore | ExperimentalArtifactStore | None = None
        #: Absolute end of the transaction lease in flight, or ``None`` when no
        #: transaction is holding the engine. While it is set, native calls are
        #: bounded by the lease and the per-call ceiling instead of by the run's
        #: remaining scheduling budget.
        self._lease_end: float | None = None
        self._lease_depth = 0
        self._lease_stats: dict[str, Any] = {
            "granted": 0, "expired": 0, "max_overrun_s": 0.0, "max_window_s": 0.0,
        }
        #: Set when the soft deadline was found to have passed; the loop uses it to
        #: stop at the next transaction boundary instead of starting more work.
        self._soft_deadline_reached = False

    def _make_artifact_store(self, run_dir: str):
        if not self.config.experimental_staging:
            store = ArtifactStore(run_dir)
        else:
            reference = self.config.acceptance_reference_board_path
            if not reference:
                raise ValueError(
                    "experimental staging requires an immutable original acceptance reference"
                )
            store = ExperimentalArtifactStore(
                run_dir, reference,
                self.config.acceptance_reference_project_path,
                self.config.acceptance_reference_rules_path,
            )
        self._active_artifact_store = store
        return store

    # -- helpers -----------------------------------------------------------

    def _open_engine(self, board_path: str) -> Any:
        if self.config.engine_factory is not None:
            return self.config.engine_factory(board_path)
        from pcb_world.engine.router_client import ipc_enabled
        if not ipc_enabled():
            raise RuntimeError(
                "the reliability runner requires an owned KiCad IPC child so "
                "native operations can be terminated at their deadlines"
            )
        from pcb_world.engine import KiCadEngine

        kwargs: dict[str, Any] = {}
        project = self.inputs.project_path if self.inputs else self.config.project_path
        input_board = self.inputs.board_path if self.inputs else self.config.board_path
        if os.path.abspath(board_path) != os.path.abspath(input_board):
            # Resuming: the board is the checkpoint's artifact, and the engine
            # associates the routing rule file with the *project it loads* - a
            # project loaded for a different board leaves no rule file at all
            # (measured: `routing_rules_loaded_from_file = False`). The artifact
            # carries its own copy of the run's project, so use that; the session
            # then proves the rules it implies are the run's resolved rules.
            sibling = os.path.splitext(board_path)[0] + ".kicad_pro"
            if os.path.basename(board_path) == "board.kicad_pcb":
                generation_project = os.path.join(
                    os.path.dirname(board_path), "board.kicad_pro"
                )
                if os.path.isfile(generation_project):
                    sibling = generation_project
            if os.path.isfile(sibling):
                project = sibling
        if project:
            kwargs["project_path"] = project
        # RouterClient resolves this callback for every dispatch, so a candidate
        # set late in the run cannot renew the budget captured at engine startup.
        kwargs["call_timeout_s"] = self._native_timeout_s
        return KiCadEngine(board_path, **kwargs)

    def _native_timeout_s(self) -> float:
        """Per-call allowance for the *next* native dispatch.

        Two regimes, because two different things are being bought.

        *No lease held* — the call is bounded by the run's remaining scheduling
        budget. That is the plain clamp, kept for callers who set
        ``transaction_lease_s=0`` and for the gate helpers, which report a
        deadline expiry as a verdict rather than as a lost transaction.

        *Lease held* — the engine is in the middle of an iteration's work, and the
        calls below it (its reads, its scan, the transaction's steps, its
        acceptance DRC, the rollback that may have to follow) are the ones whose
        reaping costs the run: the child reaped at that deadline is the child
        holding the transaction's checkpoint. The soft deadline therefore does not
        truncate them. They are bounded instead by ``engine_call_timeout_s`` per
        call and by the window's own remaining time, so nothing inside the window
        is cut short. What the window does *not* do is bound the run's total
        overrun by itself; see the bound on ``_transaction_lease``.

        A lease that has already expired is a hard-bound violation, not a
        scheduling decision: the allowance is 0, the client reaps the call, and the
        session quarantines on the state it can no longer prove — the same answer
        as any other call past its operational timeout.
        """
        ceiling_s = max(0.0, float(self.config.engine_call_timeout_s))
        if self._lease_end is not None:
            remaining = self._lease_end - self.config.wall_clock()
            if remaining <= 0.0:
                self._lease_stats["expired"] = int(self._lease_stats["expired"]) + 1
                self._publish_lease_metrics()
                return 0.0
            return min(ceiling_s, remaining)
        return min(ceiling_s, self._remaining_run_s())

    def _publish_lease_metrics(self) -> None:
        """Publish the lease accounting into the run state when one exists.

        ``granted`` counts the windows opened, ``max_window_s`` the largest single
        one (each window is individually bounded by ``_lease_seconds``), and
        ``max_overrun_s`` the *actual* largest overrun past ``time_limit_s`` seen
        when a window closed. The last two are deliberately separate figures: the
        total is not bounded by one window, so an operator reading a large
        ``max_overrun_s`` should compare it with ``max_window_s`` rather than
        assume it is one lease.
        """
        if self.state is not None:
            self.state.metrics["engine_leases"] = dict(self._lease_stats)

    def _soft_deadline_passed(self) -> bool:
        """True once the run's *scheduling* budget is spent (never a lease)."""
        return bool(self.config.time_limit_s) and self._remaining_run_s() <= 0.0

    def _lease_seconds(self) -> float:
        """How long one engine lease buys, in wall-clock seconds (0 = disabled)."""
        floor = max(0.0, float(self.config.transaction_lease_s))
        if not floor:
            return 0.0
        metrics = self.state.metrics if self.state is not None else {}
        measured = 0.0
        for key in ("source_drc_seconds", "max_attempt_seconds"):
            try:
                measured = max(measured, float(metrics.get(key, 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        return max(
            floor,
            max(0.0, float(self.config.engine_call_timeout_s)),
            LEASE_MEASURED_FACTOR * measured,
        )

    @contextmanager
    def _transaction_lease(self):
        """Hold the engine's native calls outside the soft scheduling deadline.

        One *window* per unit of work, and the window opens at the outermost
        grant: nesting it cannot extend the time already bought, so however many
        transactions a sweep goes on to try, they share the one window the
        iteration bought.

        The run opens at most two windows that can lie past ``time_limit_s``. The
        iteration's reads-and-scan window and its attempt window cannot both do so
        — ``_headroom_note`` refuses to start the attempt once the soft deadline is
        spent, so only the window that actually crossed it can — and the closing
        verification of the board the run is about to report opens one more of its
        own. The run's overrun is therefore up to **two individually bounded
        windows**, not one, and ``max_overrun_s`` reports what actually happened.
        Saved-artifact gates run outside any lease under whatever remains of the
        soft budget, so they add no overrun of their own.

        Yields whether a lease was actually held, which is all a caller needs for
        reporting.
        """
        seconds = self._lease_seconds()
        if not seconds:
            yield False
            return
        if self._lease_end is None:
            self._lease_end = self.config.wall_clock() + seconds
            self._lease_depth = 0
            self._lease_stats["granted"] = int(self._lease_stats["granted"]) + 1
            self._lease_stats["last_window_s"] = round(seconds, 3)
            self._lease_stats["max_window_s"] = round(
                max(float(self._lease_stats["max_window_s"]), seconds), 3
            )
        self._lease_depth += 1
        try:
            yield True
        finally:
            self._lease_depth -= 1
            if self._lease_depth <= 0:
                self._lease_depth = 0
                if self.config.time_limit_s:
                    overrun = max(
                        0.0, self._elapsed_s() - float(self.config.time_limit_s)
                    )
                    self._lease_stats["max_overrun_s"] = round(
                        max(float(self._lease_stats["max_overrun_s"]), overrun), 3
                    )
                self._lease_end = None
                self._publish_lease_metrics()

    def _elapsed_s(self) -> float:
        if self._run_started is None:
            return self._elapsed_prior_s
        return self._elapsed_prior_s + max(
            0.0, self.config.clock() - self._run_started
        )

    def _open_session(self, engine: Any, board_path: str) -> AgentSession:
        if self.config.session_factory is not None:
            return self.config.session_factory(engine, board_path)
        # The session proves the rule context against what the *engine* loaded,
        # which it derives from the board it opened. On a resume that board is the
        # checkpoint's artifact, whose sibling `.kicad_dru` is a copy of the run's
        # resolved rules - so the two must be compared by content, not by path.
        # (Naming the input path explicitly made every resumed mutation fail
        # closed with `rules_unavailable`.)
        session = AgentSession(
            engine, board_path=board_path,
            incremental_drc=bool(self.config.incremental_drc),
        )
        resolved = self.inputs.rules_path if self.inputs else None
        if not resolved:
            return session
        # The rule proof compares paths, and the engine's path is derived from the
        # *project it loaded* (the run's input project), not from the board it
        # opened. So the run's context is aligned to the engine's own answer -
        # after proving that answer is the run's resolved rules by content, which
        # is what makes the two paths interchangeable rather than merely similar.
        status = engine_rule_status(engine)
        loaded = str(status.get("routing_rules_path") or "")
        if not status.get("routing_rules_loaded_from_file") or not loaded:
            return session
        if not os.path.isfile(loaded):
            raise ValueError(
                f"the engine reports rule file {loaded!r}, which does not exist; "
                "refusing to continue under an unprovable rule context"
            )
        if sha256_file(loaded) != sha256_file(resolved):
            raise ValueError(
                f"the engine loaded rules {loaded!r} whose contents are not the "
                f"run's resolved rules {resolved!r}; refusing to continue under a "
                "different rule context"
            )
        session.rule_context = replace(
            session.rule_context, rules_path=loaded,
            source="runner:engine-loaded-rules",
        )
        return session

    def _safe_progress(self, session: AgentSession) -> dict[str, Any]:
        """Never query a quarantined native session after a failed proof."""
        if session.dirty:
            return dict(getattr(self, "_last_verified_progress", {}) or {})
        result = progress_summary(session)
        self._last_verified_progress = dict(result)
        return result

    def _save_best(
        self, session: AgentSession, progress: Mapping[str, Any]
    ) -> tuple[str, str]:
        """Stage, reopen, verify and atomically promote an immutable generation.

        The board comes from the engine's save; the project and rules files are
        copied from the *inputs* verbatim rather than keeping the engine's
        re-serialized project (the rules a run was accepted under must travel
        with the artifact byte-for-byte).

        Everything is written to a staging directory first and only then promoted
        over the previous artifact. Overwriting ``best_board.kicad_pcb`` in place
        before the checkpoint recorded its hash meant an interrupted save could
        destroy the only resume point the run had.
        """
        if session.dirty:
            raise RuntimeError(
                "refusing to checkpoint a quarantined session: its copper state "
                "could not be verified"
            )
        self.state.metrics["elapsed_total_s"] = self._elapsed_s()
        run_dir = self.config.run_dir
        staging = os.path.join(run_dir, "staging")
        os.makedirs(staging, exist_ok=True)
        staged_board = os.path.join(staging, "best_board.kicad_pcb")
        staged_project = os.path.join(staging, "best_board.kicad_pro")
        staged_rules = os.path.join(staging, "best_board.kicad_dru")

        session._engine.save(staged_board, staged_project)
        project_src = self.inputs.project_path if self.inputs else self.config.project_path
        rules_src = self.inputs.rules_path if self.inputs else self.config.rules_path
        if project_src and os.path.isfile(project_src):
            shutil.copyfile(project_src, staged_project)
        elif os.path.isfile(staged_project):
            os.remove(staged_project)      # never ship the engine's re-serialization
        if rules_src and os.path.isfile(rules_src):
            shutil.copyfile(rules_src, staged_rules)

        board_hash = sha256_file(staged_board)
        if not board_hash or not os.path.getsize(staged_board):
            raise RuntimeError("the engine wrote an empty board; nothing promoted")
        verification = self._verify_sidecars(
            staged_project, staged_rules, project_src, rules_src
        )
        if not verification.get("ok", True):
            raise RuntimeError(
                "artifact verification failed; the previous checkpoint is kept "
                f"({verification.get('reason', 'unknown')})"
            )

        if self.config.experimental_staging:
            store = self._active_artifact_store
            if not isinstance(store, ExperimentalArtifactStore):
                raise RuntimeError("experimental staging store was not initialized")

            def checkpoint_for(folder: str, manifest: Mapping[str, Any]) -> dict:
                """Checkpoint naming the generation being written, not the last one.

                The pointer is the resume authority for a staging run, so the
                state it carries must already name this generation's board and
                hash. Writing ``self.state`` before those fields were set made a
                crash-resume read a checkpoint that still pointed at the previous
                best board.
                """
                board_path = os.path.join(folder, "board.kicad_pcb")
                board_sha = manifest["files"]["board.kicad_pcb"]
                self.state.best_board_path = board_path
                self.state.best_board_sha256 = board_sha
                self.state.best_progress = dict(progress)
                checkpoint = self.state.to_dict()
                checkpoint.update({
                    "best_board_path": board_path,
                    "best_board_sha256": board_sha,
                    "best_progress": dict(progress),
                })
                return checkpoint

            folder, manifest = store.stage(
                staged_board,
                staged_project if os.path.isfile(staged_project) else None,
                staged_rules if os.path.isfile(staged_rules) else None,
                checkpoint=checkpoint_for,
                evidence={
                    "local_native_attempt_gate": "passed for staged session",
                    "global_acceptance": "not run; final original-reference gates required",
                    "sidecar_verification": verification,
                },
            )
            self.state.metrics["experimental_staging"] = manifest
            self.state.metrics["artifact"] = manifest
            board_path = os.path.join(folder, "board.kicad_pcb")
            board_hash = sha256_file(board_path) or ""
            # Keep the in-session state and the durable checkpoint identical.
            self.state.best_board_path = board_path
            self.state.best_board_sha256 = board_hash
            self.state.best_progress = dict(progress)
            return board_path, board_hash

        manifest = {
            "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "progress": dict(progress),
            "inputs": self.inputs.to_evidence() if self.inputs else {},
            "verification": verification,
        }
        store = ArtifactStore(run_dir)
        folder, manifest = store.stage(
            staged_board, staged_project if os.path.isfile(staged_project) else None,
            staged_rules if os.path.isfile(staged_rules) else None, manifest,
        )
        geometry_digest = session.board_digest()
        if not geometry_digest:
            raise RuntimeError("candidate geometry digest is unreadable; refusing promotion")
        baseline_evidence = getattr(self, "_acceptance_baseline_evidence", None)
        if not isinstance(baseline_evidence, dict):
            raise RuntimeError(
                "source native DRC baseline evidence is unavailable; refusing "
                "promotion (recapture it under the collision-aware policy)"
            )
        request = {
            "mode": "verify_generation",
            "generation_dir": folder,
            "geometry_digest": geometry_digest,
            "expected_progress": dict(progress),
            "baseline": baseline_evidence,
        }
        if self.config.artifact_verifier is not None:
            reopened = dict(self.config.artifact_verifier(folder, request))
        elif self.config.engine_factory is not None or not self.config.verify_artifacts:
            raise RuntimeError(
                "saved-artifact acceptance requires a production native verifier; "
                "custom engines must inject an explicit test verifier"
            )
        else:
            reopened = self._verify_generation_in_child(folder, request)
        if not reopened.get("ok"):
            raise RuntimeError(
                "fresh-process saved-artifact acceptance failed; previous accepted "
                f"generation remains active: {reopened}"
            )
        manifest["saved_artifact_verification"] = reopened
        # Terminal relations are compared by a fresh two-board native process over
        # the frozen reference and this candidate: counts cannot see a swap, and a
        # partition captured earlier in the run would not be evidence about the
        # saved bytes.
        if self.config.terminal_verifier is not None:
            terminal = dict(self.config.terminal_verifier(folder, request))
        elif self.config.artifact_verifier is not None:
            terminal = dict(reopened.get("terminal_partition") or {})
        elif self.config.engine_factory is not None or not self.config.verify_artifacts:
            raise RuntimeError(
                "saved-artifact acceptance requires a production native terminal "
                "verifier; custom engines must inject an explicit test verifier"
            )
        else:
            terminal = self._terminal_partition_in_child(folder)
        manifest["terminal_partition_verification"] = terminal
        if not (terminal.get("ok") and terminal.get("complete")):
            raise RuntimeError(
                "fresh native terminal-partition acceptance failed; previous "
                "accepted generation remains active: "
                f"{'; '.join(str(r) for r in (terminal.get('reasons') or [])[:3])}"
            )
        # The native gate is authoritative for physics; the installed CLI is an
        # *additional* gate whose verdict is recorded either way. A native-only run
        # says so rather than implying a CLI acceptance it never made.
        cli_evidence = self._installed_cli_verdict(folder, terminal_proof=terminal)
        manifest["installed_cli_verification"] = cli_evidence
        cli_status = str(cli_evidence.get("status", "not_configured"))
        cli_required = bool(cli_evidence.get("required"))
        if cli_status == "regressed" or (cli_required and not cli_evidence.get("ok")):
            raise RuntimeError(
                "installed-CLI acceptance failed; previous accepted generation "
                f"remains active ({cli_status}: "
                f"{'; '.join(str(r) for r in (cli_evidence.get('reasons') or [])[:3])})"
            )
        manifest_path = os.path.join(folder, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        self.state.metrics["artifact"] = manifest
        self._artifact_confirmation = reopened
        board_path = os.path.join(folder, "board.kicad_pcb")
        board_hash = sha256_file(board_path) or ""
        checkpoint = self.state.to_dict()
        checkpoint.update({
            "best_board_path": board_path,
            "best_board_sha256": board_hash,
            "best_progress": dict(progress),
        })
        store.promote(folder, manifest, checkpoint=checkpoint)
        self.state.best_board_path = board_path
        self.state.best_board_sha256 = board_hash
        self.state.best_progress = dict(progress)
        return board_path, board_hash

    @staticmethod
    def _json_key(value: Any) -> Any:
        if isinstance(value, tuple):
            return [RoutingRunner._json_key(item) for item in value]
        if isinstance(value, list):
            return [RoutingRunner._json_key(item) for item in value]
        return value

    def _installed_cli_verdict(
        self, folder: str, terminal_proof: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the installed-CLI gate for one staged generation, or say why not.

        The source it is compared against is the run's immutable input board, not
        the previous generation: acceptance is always "no worse than the board
        this run started from". ``terminal_proof`` is the fresh native
        terminal-partition evidence for this candidate; only the reporter's
        churned ``unconnected_items`` endpoint pairing may lean on it.
        """
        if self.config.cli_gate is None and self.config.cli_verifier is None:
            return not_configured_verdict().to_evidence()
        source_board = self.inputs.board_path if self.inputs else self.config.board_path
        source_rules = self.inputs.rules_path if self.inputs else self.config.rules_path
        candidate_board = os.path.join(folder, "board.kicad_pcb")
        candidate_rules = os.path.join(folder, "board.kicad_dru")
        work_dir = os.path.join(folder, "cli_reports")
        kwargs = dict(
            source_board=source_board, candidate_board=candidate_board,
            source_rules=source_rules,
            candidate_rules=candidate_rules if os.path.isfile(candidate_rules) else None,
            source_project=self.inputs.project_path if self.inputs else self.config.project_path,
            candidate_project=(os.path.join(folder, "board.kicad_pro")
                               if os.path.isfile(os.path.join(folder, "board.kicad_pro"))
                               else None),
            work_dir=work_dir,
            terminal_partition_proof=terminal_proof,
        )
        if self.config.cli_verifier is not None:
            return dict(self.config.cli_verifier(**kwargs))
        gate_config = replace(
            self.config.cli_gate, remaining_budget_s=self._remaining_run_s,
        )
        verdict = run_gate(gate_config, **kwargs)
        return verdict.to_evidence()

    def _remaining_run_s(self) -> float:
        """Run budget left for multi-process gates, not just native calls."""
        if not self.config.time_limit_s:
            return float("inf")
        return max(0.0, float(self.config.time_limit_s) - self._elapsed_s())

    def _attempt_headroom_s(self) -> float:
        """Run budget one more pair attempt needs before it is started.

        The bound is the worst cost this run has already paid for either of the
        two things an attempt cannot be interrupted inside: a whole-board
        acceptance DRC (measured once at startup) or a whole attempt (measured as
        attempts complete), scaled by ``attempt_headroom_factor`` and floored by
        ``attempt_headroom_s``. Both measurements are wall-clock.

        A run with neither measurement yet — the first iteration of a run whose
        board is cheap to check — has no evidence that an attempt is expensive, so
        the requirement is the floor. The floor is deliberately non-zero: the
        loop's own reads and its scan are work too, and this guard is a
        *scheduling* check rather than the guarantee (the lease is the guarantee).
        """
        config = self.config
        if not config.time_limit_s:
            return 0.0
        measured = 0.0
        for key in ("source_drc_seconds", "max_attempt_seconds"):
            try:
                measured = max(measured, float(self.state.metrics.get(key, 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        return max(
            max(0.0, float(config.attempt_headroom_s)),
            max(0.0, float(config.attempt_headroom_factor)) * measured,
        )

    def _headroom_note(self) -> str:
        """Why the loop must not start more work, or ``""`` when it may.

        Called twice per iteration: once before the scan, and once after it,
        because the scan's own cost is budget the attempt below can no longer
        spend. A run that stops here stops *before* starting more work, which is
        how it keeps near its soft deadline; it is not what makes an attempt that
        outgrows its history verifiable — the lease is.

        The soft deadline is also an absolute "do not start" line, independent of
        the measured requirement: once it has passed, an attempt started now would
        be a second lease window past the deadline on top of the closing
        verification's, and it is work the next boundary check is about to stop
        anyway. That is what keeps the run's overrun to at most two individually
        bounded windows instead of one per unit of work.
        """
        required_s = self._attempt_headroom_s()
        remaining_s = self._remaining_run_s()
        if remaining_s > 0.0 and (not required_s or remaining_s >= required_s):
            return ""
        self.state.metrics["time_limit_headroom"] = {
            "remaining_s": round(remaining_s, 3),
            "required_s": round(required_s, 3),
            "soft_deadline_spent": remaining_s <= 0.0,
            "source_drc_seconds": self.state.metrics.get("source_drc_seconds"),
            "max_attempt_seconds": self.state.metrics.get("max_attempt_seconds"),
            "attempt_headroom_s": float(self.config.attempt_headroom_s),
            "attempt_headroom_factor": float(self.config.attempt_headroom_factor),
        }
        if remaining_s <= 0.0:
            detail = "the run's time limit is spent"
        else:
            detail = (
                f"{remaining_s:.1f} s of the run budget left, and one more attempt "
                f"needs about {required_s:.1f} s to be verifiable"
            )
        return f"stopped before mutating copper: {detail}"

    def _baseline_gate_failed(
        self, session: AgentSession, exc: Exception, progress: Mapping[str, Any]
    ) -> RunReport:
        """The board could not pass the acceptance gate before routing started.

        This is a fail-closed stop with a report, not a crash: nothing is
        promoted, the reason names which gate refused, and the state records that
        no best artifact exists. It is reachable when the installed-CLI gate is
        required and unusable (for example no CLI installed), which must be a
        precise ``blocked`` outcome rather than an exception out of the runner.
        """
        report = RunReport(
            status="blocked", stop_reason="artifact_verification_failed",
            progress_before=dict(progress), progress_after=dict(progress),
        )
        self._stop_requested = "artifact_verification_failed"
        detail = f"{type(exc).__name__}: {exc}"
        self.state.status = report.status
        self.state.stop_reason = report.stop_reason
        self.state.metrics["artifact_verification_failure"] = detail
        report.notes.append(
            "the board did not pass the acceptance gate (fresh native saved-artifact "
            f"verification plus the configured installed-CLI gate): {detail}"
        )
        report.best_board_path = self.state.best_board_path
        report.best_board_sha256 = self.state.best_board_sha256
        self.state.save()
        return report

    def _verify_generation_in_child(
        self, folder: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Run the production KiCad reopen/DRC gate in an owned child process."""
        return self._run_saved_artifact_child(folder, request)

    def _run_saved_artifact_child(
        self, folder: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Run one saved-artifact verifier mode in an owned child process."""
        timeout_s = self._native_timeout_s()
        if timeout_s <= 0:
            return {"ok": False, "reason": "run deadline expired before artifact verification"}
        request = {**dict(request), "call_timeout_s": timeout_s}
        # Name the reference this gate compares against so the child can hash it
        # itself; the evidence then binds the exact frozen board, not a path the
        # caller asserted.
        if self.config.experimental_staging:
            request.setdefault("reference_board_path",
                               self.config.acceptance_reference_board_path)
            request.setdefault("reference_project_path",
                               self.config.acceptance_reference_project_path)
            request.setdefault("reference_rules_path",
                               self.config.acceptance_reference_rules_path)
        else:
            inputs = self.inputs
            request.setdefault("reference_board_path",
                               inputs.board_path if inputs else self.config.board_path)
            request.setdefault("reference_project_path",
                               inputs.project_path if inputs else self.config.project_path)
            request.setdefault("reference_rules_path",
                               inputs.rules_path if inputs else self.config.rules_path)
        request_path = os.path.join(folder, "verify_request.json")
        with open(request_path, "w", encoding="utf-8") as handle:
            json.dump(request, handle, sort_keys=True)
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "tools", "reliability", "verify_saved_artifact.py",
        )
        command = [sys.executable, script, request_path]
        try:
            result = runner_run_owned_process(command, timeout_s=timeout_s)
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "saved-artifact verifier deadline exceeded"}
        finally:
            try:
                os.unlink(request_path)
            except OSError:
                pass
        try:
            evidence = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            evidence = {"ok": False, "reason": "verifier returned no machine-readable result",
                        "returncode": result.returncode,
                        "stderr_tail": result.stderr[-4000:],
                        "stdout": result.stdout[-1000:], "command": command}
        if result.returncode != 0:
            evidence["ok"] = False
            evidence.setdefault("reason", evidence.get("error", "verifier failed"))
        return evidence

    def _terminal_partition_in_child(self, folder: str) -> dict[str, Any]:
        """Fresh two-board terminal-partition gate for one staged generation."""
        return self._run_saved_artifact_child(folder, {
            "mode": "terminal_partition",
            "generation_dir": folder,
        })

    def _capture_source_drc_baseline(self) -> dict[str, Any]:
        """Capture a source DRC baseline that another process can replay exactly."""
        timeout_s = self._native_timeout_s()
        if timeout_s <= 0:
            raise RuntimeError("source DRC baseline capture has no run time remaining")
        request = {
            "mode": "capture_baseline",
            "board_path": self.inputs.board_path,
            "project_path": self.inputs.project_path,
            "rules_path": self.inputs.rules_path,
            "call_timeout_s": timeout_s,
        }
        fd, request_path = tempfile.mkstemp(prefix="pcbworld-baseline-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(request, handle, sort_keys=True)
            script = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "tools", "reliability", "verify_saved_artifact.py",
            )
            result = runner_run_owned_process(
                [sys.executable, script, request_path], timeout_s=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("source DRC baseline capture exceeded its deadline") from exc
        finally:
            try:
                os.unlink(request_path)
            except OSError:
                pass
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError("source DRC baseline capture returned no evidence") from exc
        if result.returncode or not payload.get("ok"):
            raise RuntimeError(f"source DRC baseline capture failed: {payload}")
        baseline = payload.get("baseline")
        if not isinstance(baseline, Mapping) or not isinstance(
            baseline.get("baseline"), Mapping
        ):
            raise RuntimeError(
                "source DRC baseline capture returned no bound baseline envelope"
            )
        # The envelope travels - the violations plus the bytes and build they were
        # measured with - so the saved-artifact gate can re-check both before it
        # replays a single identity.
        return dict(baseline)

    def _verify_sidecars(
        self,
        staged_project: str,
        staged_rules: str,
        project_src: str | None,
        rules_src: str | None,
    ) -> dict[str, Any]:
        """The sidecars a promotion ships must be byte-identical to the inputs.

        Copying the engine's re-serialized project instead of the input project
        is a real defect (the artifact is then checked against settings the run
        never used), so the byte comparison is the gate on promotion.
        """
        evidence: dict[str, Any] = {
            "ok": True,
            "project_matches_input": None,
            "rules_matches_input": None,
        }
        if project_src:
            evidence["project_matches_input"] = bool(
                os.path.isfile(staged_project)
                and sha256_file(project_src) == sha256_file(staged_project)
            )
            if not evidence["project_matches_input"]:
                return {**evidence, "ok": False,
                        "reason": "project sidecar differs from the input"}
        if rules_src:
            evidence["rules_matches_input"] = bool(
                os.path.isfile(staged_rules)
                and sha256_file(rules_src) == sha256_file(staged_rules)
            )
            if not evidence["rules_matches_input"]:
                return {**evidence, "ok": False,
                        "reason": "rules sidecar differs from the input"}
        return evidence

    def _reopen_artifact(
        self, progress: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Read the fresh-process acceptance result stored by the pointer."""
        try:
            store = self._active_artifact_store or self._make_artifact_store(self.config.run_dir)
            current = store.current()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reopened": False,
                    "reason": f"accepted pointer invalid: {type(exc).__name__}: {exc}"}
        if current is None:
            return {"ok": False, "reopened": False,
                    "reason": "no fresh-process verified artifact is active"}
        folder, manifest = current
        if self.config.experimental_staging:
            return {
                "ok": True,
                "reopened": False,
                "status": "experimental_staging_only",
                "accepted": False,
                "generation_dir": folder,
                "original_reference_sha256": manifest.get("original_reference_sha256"),
                "reason": "durable local checkpoint; no global acceptance is claimed",
            }
        result = dict(manifest.get("saved_artifact_verification", {}))
        result["generation_dir"] = folder
        result["reopened"] = bool(result.get("ok"))
        saved_progress = dict(manifest.get("progress", {}))
        if progress is not None and dict(progress) != saved_progress:
            result.update({
                "ok": False,
                "reopened": False,
                "reason": "report progress does not match the active artifact generation",
                "expected_progress": dict(progress),
                "saved_progress": saved_progress,
            })
        result["active_artifact_progress"] = saved_progress
        return result

    def _record(
        self,
        pair: NetPair,
        candidate: Candidate | None,
        result: Mapping[str, Any],
        *,
        source: str,
        duration_s: float,
        reason: str = "",
        probe_only: bool = False,
        sweep_member: bool = False,
        plan_key: tuple | None = None,
        offered_pair_key: tuple = (),
    ) -> AttemptRecord:
        evidence = result.get("evidence", {}) if isinstance(result, Mapping) else {}
        delta = evidence.get("drc_delta", {}) if isinstance(evidence, Mapping) else {}
        # The class breakdown is taken from the delta's own complete histogram when
        # the session supplies one. The `added_relevant` rows below are a capped
        # sample (the delta keeps a few positions rather than an unbounded block),
        # so counting those rows under-reports a refusal's classes: the phase-17
        # trial recorded a delta that added 50 relevant findings as eight. The rows
        # are still read for the hint positions, which are deliberately the first
        # few, and as the fallback histogram for a payload that predates the field.
        classes: dict[str, int] = {}
        hints: list[tuple[str, float, float, int]] = []
        complete_classes = delta.get("added_relevant_class_counts")
        if isinstance(complete_classes, Mapping):
            for name, count in complete_classes.items():
                try:
                    classes[str(name)] = int(count)
                except (TypeError, ValueError):
                    continue
        for row in (delta.get("added_relevant") or []):
            if not isinstance(row, Mapping):
                continue
            error_type = str(row.get("error_type", ""))
            if not isinstance(complete_classes, Mapping):
                classes[error_type] = classes.get(error_type, 0) + 1
            if len(hints) < 4:
                try:
                    hints.append((error_type, float(row.get("x_mm", 0.0)),
                                  float(row.get("y_mm", 0.0)),
                                  int(row.get("layer", 0))))
                except (TypeError, ValueError):
                    continue
        steps = list(result.get("steps", []) or [])
        applied = [step for step in steps
                   if bool((step or {}).get("success", True))]
        # The session stops its plan at the first step that did not succeed, so
        # the first unsuccessful entry is where the transaction stopped. Kept on
        # the record because ``steps_applied`` alone cannot separate a route that
        # never opened from a via that never landed, which forced a failure
        # analysis to replay the plan against a harness that may have moved on
        # (see docs/agent-work/reliability/phase17/PLAN.md).
        failed_step_index = next(
            (index for index, step in enumerate(steps)
             if not bool((step or {}).get("success", True))),
            -1,
        )
        failed_step_kind = (
            str((steps[failed_step_index] or {}).get("kind", "") or "")
            if failed_step_index >= 0 else ""
        )
        # ``committed`` is three-valued in the session (None = the session could
        # not establish it). The record is a bool, so None becomes False, but the
        # copper state is carried through so a quarantine is never lost.
        raw_committed = result.get("committed", None) if isinstance(result, Mapping) else None
        if probe_only:
            committed = False
        elif raw_committed is None:
            committed = bool(result.get("accepted"))
        else:
            committed = bool(raw_committed)
        record = AttemptRecord(
            pair_key=pair.key,
            plan_key=(
                plan_key if plan_key is not None
                else (candidate.key() if candidate is not None else ())
            ),
            candidate=candidate.name if candidate is not None else "planner",
            mode=candidate.mode if candidate is not None else str(result.get("mode", "")),
            kind=candidate.kind if candidate is not None else "planner",
            source=source,
            outcome=str(result.get("outcome", "")),
            accepted=bool(result.get("accepted")),
            connected=bool(result.get("connected")),
            reason=reason or str(evidence.get("reason", "")),
            added_relevant=int(delta.get("added_relevant_count") or 0),
            vias_added=int(evidence.get("vias_added") or 0),
            added_length_mm=float(evidence.get("added_length_mm") or 0.0),
            changed_nets=tuple(int(n) for n in evidence.get("changed_nets", []) or []),
            duration_s=duration_s,
            probe_only=probe_only,
            sweep_member=sweep_member,
            offer_source=str(pair.source),
            component_start=str(pair.component_start),
            component_target=str(pair.component_target),
            substituted=bool(pair.substituted),
            # ``pair.key`` stays the attempted geometry; the offered edge the
            # attempt was made for is carried separately, so a substitution is
            # attributable to the connection the scan actually drew.
            offered_pair_key=(
                tuple(offered_pair_key) if offered_pair_key
                else offered_edge_key(pair)
            ),
            drc_classes=tuple(sorted(classes.items())),
            drc_hints=tuple(hints),
            closed_before_refusal=bool(evidence.get("connected_before_refusal")),
            committed=committed,
            board_digest=self._pending_digest,
            copper_state=(
                str(result.get("copper_state"))
                if result.get("copper_state") is not None else None
            ),
            steps_applied=len(applied),
            failed_step_kind=failed_step_kind,
            failed_step_index=failed_step_index,
            # The failure's own words. Without these the run state kept only the
            # category (``drc_unavailable`` / ``retained_unknown``) and the exact
            # cause had to be re-derived by reproducing the run — see
            # docs/agent-work/reliability/phase8/DECISION.md.
            failure_exception=compact_exception_text(evidence.get("exception")),
            rollback_detail=compact_rollback_detail(evidence.get("rollback")),
        )
        self.state.attempts.note(record)
        return record

    def _digest(self, session: AgentSession) -> str | None:
        """Board geometry digest, shared by the attempt records and the scan."""
        digest = session.board_digest()
        self._pending_digest = digest
        return digest

    # -- main loop ---------------------------------------------------------

    def run(self) -> RunReport:
        cfg = self.config
        os.makedirs(cfg.run_dir, exist_ok=True)
        # Determinism: a reused engine server carries a shifted UUID stream, which
        # perturbs UUID-keyed ordering (and a handful of DRC findings on a large
        # board). A routing run wants a fresh server per engine unless the
        # operator deliberately asked otherwise.
        os.environ.setdefault("KICAD_ENGINE_REUSE", "0")
        # One resolution of the board's sidecars, used for the provenance, the
        # session's rule context, and the artifact copy.
        self.inputs = resolve_inputs(
            cfg.board_path, project_path=cfg.project_path, rules_path=cfg.rules_path
        )
        provenance = build_provenance(
            board_path=self.inputs.board_path, project_path=self.inputs.project_path,
            rules_path=self.inputs.rules_path,
        )
        if self.state is None:
            self.state = RunState(
                board_path=self.inputs.board_path, project_path=self.inputs.project_path,
                rules_path=self.inputs.rules_path, provenance=provenance,
                run_dir=cfg.run_dir,
                progress=ProgressTracker(patience=cfg.stall_patience),
                model_usage={
                    "requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "categories": {},
                },
            )
        self.state.metrics["inputs"] = self.inputs.to_evidence()
        # A resumed checkpoint carries the stall count it had, but the *operator's*
        # patience for this session is what should govern: inheriting the
        # checkpoint's own (often smaller) value made a resumed run stop after one
        # attempt however large its --stall-patience was.
        if self.state.progress.patience != cfg.stall_patience:
            self.state.metrics["resumed_stall_patience"] = {
                "checkpoint": self.state.progress.patience,
                "requested": cfg.stall_patience,
            }
            self.state.progress.patience = cfg.stall_patience
        # The stall count is "fruitless attempts in this session", not a lifetime
        # total: carried across a resume it stops a new session after a couple of
        # attempts however large its own bounds are, which is not what the
        # operator asked for. The attempt budget still bounds the whole run.
        if self.state.progress.stagnation:
            self.state.metrics["resumed_stall_reset"] = self.state.progress.stagnation
            self.state.progress.stagnation = 0
        self.state.metrics.setdefault("determinism", {
            "engine_reuse": os.environ.get("KICAD_ENGINE_REUSE", ""),
            "note": "KICAD_ENGINE_REUSE=0 gives each engine a fresh server",
        })

        # A checkpoint owns its run directory: ``state.save()`` writes the
        # checkpoint back where it came from, while the best-board artefacts are
        # written into ``cfg.run_dir``. Letting the two differ would scatter one
        # run across two directories while the report claims paths in both, so
        # refuse instead of guessing which the operator meant.
        if self.state is not None and (
            os.path.abspath(self.state.run_dir) != os.path.abspath(cfg.run_dir)
        ):
            raise ValueError(
                "resume refused: the checkpoint belongs to a different run "
                f"directory ({self.state.run_dir}); resuming into {cfg.run_dir} "
                "would write the state file and the board artefacts to different "
                "places. Pass the checkpoint's own run directory, or copy that "
                "directory and resume the copy."
            )

        artifact_store = self._make_artifact_store(cfg.run_dir)
        artifact_store.snapshot_pointer()
        current_artifact = artifact_store.current()
        if current_artifact is not None:
            generation, manifest = current_artifact
            self.state.best_board_path = os.path.join(generation, "board.kicad_pcb")
            self.state.best_board_sha256 = manifest["files"]["board.kicad_pcb"]
            self.state.best_progress = dict(manifest.get("progress", {}))
            # The atomic pointer is authoritative if a crash happened between
            # promotion and saving run_state.json.
            self.state.metrics["artifact"] = manifest

        # Direct API callers can pass a deserialized RunState and bypass the
        # CLI's expected_provenance check. Recompute at the trust boundary every
        # time; paths alone are not evidence that the source inputs are equal.
        differing = {
            key: {"checkpoint": self.state.provenance.get(key), "current": value}
            for key, value in provenance.items()
            if self.state.provenance.get(key) != value
        }
        if differing:
            raise ValueError(
                "resume refused: source PCB, effective project/rules, or engine "
                f"provenance changed ({sorted(differing)})"
            )
        pointer_state = artifact_store.last_checkpoint
        if pointer_state is not None:
            pointer_prov = dict(pointer_state.get("provenance", {}))
            if pointer_prov != provenance:
                raise ValueError(
                    "resume refused: the atomic artifact pointer's checkpoint "
                    "provenance differs from current inputs"
                )
            pointer_iteration = int(
                dict(pointer_state.get("metrics", {})).get("iterations", 0)
            )
            state_iteration = int(self.state.metrics.get("iterations", 0))
            if pointer_iteration <= state_iteration:
                pointer_state = None
        if pointer_state is not None:
            # Promotion and run_state.json are separate files. The pointer carries
            # the checkpoint snapshot committed with its generation so a crash
            # between those writes cannot lose the accepted attempt/accounting.
            self.state.attempts = AttemptHistory.from_dicts(
                pointer_state.get("attempts", [])
            )
            self.state.progress = ProgressTracker.from_dict(
                pointer_state.get("progress", {})
            )
            self.state.metrics.update(dict(pointer_state.get("metrics", {})))
            self.state.model_usage = dict(pointer_state.get("model_usage", {}))
            self.state.status = str(pointer_state.get("status", self.state.status))
            self.state.stop_reason = str(pointer_state.get("stop_reason", ""))
            self.state.best_progress = dict(pointer_state.get("best_progress", {}))
            if self.state.progress.patience != cfg.stall_patience:
                self.state.metrics["resumed_stall_patience"] = {
                    "checkpoint": self.state.progress.patience,
                    "requested": cfg.stall_patience,
                }
                self.state.progress.patience = cfg.stall_patience
            if self.state.progress.stagnation:
                self.state.metrics["resumed_stall_reset"] = self.state.progress.stagnation
                self.state.progress.stagnation = 0

        # Resume from the best verified board when the checkpoint has one.
        start_board = cfg.board_path
        if self.state.best_board_path:
            # A copied checkpoint carries paths from the directory it was written
            # in; prefer the copy's own artifact of the same name so a branched run
            # works on its own files (the hash check below still applies).
            relative = os.path.relpath(self.state.best_board_path, self.state.run_dir)
            local = os.path.normpath(os.path.join(cfg.run_dir, relative))
            if (
                os.path.abspath(local) != os.path.abspath(self.state.best_board_path)
                and os.path.isfile(local)
            ):
                self.state.best_board_path = local
        if self.state.best_board_path and os.path.isfile(self.state.best_board_path):
            recorded = self.state.best_board_sha256
            actual = sha256_file(self.state.best_board_path)
            if recorded and actual != recorded:
                raise ValueError(
                    "resume refused: the checkpoint's best board hash does not match "
                    f"the file ({self.state.best_board_path})"
                )
            start_board = self.state.best_board_path
        elif self.state.best_board_path:
            raise ValueError(
                "resume refused: the checkpoint names a best artifact that is "
                f"missing: {self.state.best_board_path}"
            )

        self._elapsed_prior_s = float(self.state.metrics.get("elapsed_total_s", 0.0))
        started = cfg.clock()
        self._run_started = started
        engine = self._open_engine(start_board)
        local_report: RunReport | None = None
        try:
            session = self._open_session(engine, start_board)
            self.resolver = LayerResolver.build(session)
            rules_path = self._effective_rules_path(session)
            # Time this whole-board DRC: it is the pass every accepted
            # transaction has to pay for again at acceptance, and its cost on
            # this board is what tells the loop how much run budget an attempt
            # needs (``_attempt_headroom_s``). Only a run that actually measured
            # it in this process records it, so a resumed run keeps the first
            # measurement rather than a rate that changed with the copper.
            # Wall-clock, not ``cfg.clock``: the measurement is a real duration,
            # and a test double's fake run clock must not inflate it.
            drc_started = cfg.wall_clock()
            baseline = take_violations(engine, rules_path)
            self.state.metrics.setdefault(
                "source_drc_seconds",
                round(max(0.0, cfg.wall_clock() - drc_started), 3),
            )
            if "source_drc_baseline" not in self.state.metrics:
                if os.path.abspath(start_board) == os.path.abspath(self.inputs.board_path):
                    # Captured in this process from the board the run started on,
                    # with the inventory the gate compared against. The envelope
                    # names the input bytes and this process's router build, both of
                    # which the verifying child re-measures.
                    rules_status = engine_rule_status(engine)
                    evidence = capture_envelope(
                        baseline,
                        board_path=self.inputs.board_path,
                        project_path=self.inputs.project_path,
                        rules_path=self.inputs.rules_path,
                        rules_evidence=rules_status,
                    )
                else:
                    evidence = self._capture_source_drc_baseline()
                self.state.metrics["source_drc_baseline"] = evidence
            self._acceptance_baseline_evidence = dict(
                self.state.metrics["source_drc_baseline"]
            )
            # The terminal partition of the board this run started from is the
            # connectivity half of acceptance. It is captured once, from the
            # accepted reference, and compared at every promotion by the
            # fresh-process saved-artifact gate.
            if self._acceptance_reference_terminals is None:
                try:
                    from pcb_world.agent.terminals import capture_terminals

                    captured = capture_terminals(engine)
                    reference_partition = captured.to_evidence()
                    if not captured.complete or not captured.terminals:
                        raise RuntimeError(
                            "; ".join(captured.reasons)
                            or "native terminal inventory was empty"
                        )
                except Exception:  # noqa: BLE001 - absence is recorded, not guessed
                    reference_partition = None
                    terminal_error = "native terminal partition could not be proven"
                else:
                    terminal_error = ""
                if reference_partition is None:
                    self.state.metrics["terminal_reference"] = (
                        f"unavailable: {terminal_error}"
                    )
                    if self.config.engine_factory is None:
                        local_report = self._baseline_gate_failed(
                            session,
                            RuntimeError(self.state.metrics["terminal_reference"]),
                            progress_summary(session),
                        )
                        return local_report
                else:
                    self.state.metrics["terminal_reference"] = {
                        "nets": reference_partition["nets"],
                        "terminals": reference_partition["terminals"],
                        "clusters": reference_partition["clusters"],
                    }
                self._acceptance_reference_terminals = reference_partition
            progress_before = progress_summary(session)
            self._last_verified_progress = dict(progress_before)
            if (current_artifact is None and self.state.best_board_path
                    and os.path.isfile(self.state.best_board_path)):
                # Migrate a legacy mutable best-board checkpoint into an
                # immutable generation only after fresh native verification.
                try:
                    migrated_path, migrated_hash = self._save_best(
                        session, progress_before
                    )
                except Exception as exc:  # noqa: BLE001 - baseline gate is fail-closed
                    local_report = self._baseline_gate_failed(
                        session, exc, progress_before
                    )
                    return local_report
                self.state.best_board_path = migrated_path
                self.state.best_board_sha256 = migrated_hash
                self.state.best_progress = dict(progress_before)
            elif current_artifact is None:
                # The source board itself must pass the same saved-artifact gate
                # before any candidate can replace it.
                try:
                    source_path, source_hash = self._save_best(
                        session, progress_before
                    )
                except Exception as exc:  # noqa: BLE001 - baseline gate is fail-closed
                    local_report = self._baseline_gate_failed(
                        session, exc, progress_before
                    )
                    return local_report
                self.state.best_board_path = source_path
                self.state.best_board_sha256 = source_hash
                self.state.best_progress = dict(progress_before)
            accepted_at_run_start = artifact_store.snapshot_pointer()
            accepted_checkpoint = (
                json.loads(accepted_at_run_start).get("checkpoint")
                if accepted_at_run_start else None
            )
            self.state.save()
            if self.state.best_progress is None:
                self.state.best_progress = dict(progress_before)
            if self.state.progress.best is None:
                self.state.progress.best = dict(progress_before)

            report = local_report = RunReport(
                status="running", stop_reason="",
                progress_before=dict(progress_before),
                model_usage=dict(self.state.model_usage),
                best_board_path=self.state.best_board_path,
                best_board_sha256=self.state.best_board_sha256,
            )

            # ``max_attempts`` bounds real attempts (one deterministic apply or
            # one planner call); the individual candidates of a deterministic
            # sweep are counted separately so a six-candidate set is one attempt.
            iterations = int(self.state.metrics.get("iterations", 0))
            run_attempt_start = len(self.state.attempts.records)
            report.attempts = iterations
            report.plan_evaluations = len(self.state.attempts.records)

            stop_reason = ""
            while True:
                if iterations >= cfg.max_attempts:
                    stop_reason = "attempt_limit"
                    break
                if cfg.time_limit_s and self._elapsed_s() >= cfg.time_limit_s:
                    stop_reason = "time_limit"
                    break
                if cfg.max_total_tokens and self.state.model_usage["prompt_tokens"] + \
                        self.state.model_usage["completion_tokens"] >= cfg.max_total_tokens:
                    stop_reason = "token_limit"
                    break

                # Before the scan: it is native work under the same deadline
                # clamp, so starting one the budget cannot cover is the same
                # mistake the guard exists to prevent.
                headroom_note = self._headroom_note()
                if headroom_note:
                    stop_reason = "time_limit_headroom"
                    report.notes.append(headroom_note)
                    break

                # The digest, the scan and the outstanding count are native work
                # too. They run under the same lease as the attempt they select,
                # because a read reaped at the soft deadline is what ended an
                # earlier segment of this campaign from inside ``get_pad_groups``.
                with self._transaction_lease():
                    # A pair is skipped only when this run has spent its per-pair
                    # budget on the *current* board: a plan that failed against
                    # other copper may work after the board changed, so failures
                    # are filed against the geometry they were measured on.
                    digest = self._digest(session)
                    # Which families a pair's plan keys can come from depends on
                    # the copper layer count; the selector needs it to tell an
                    # *unanswered* refusal from one the sweep already answered.
                    self._copper_layers_cache = int(
                        session._engine.get_copper_layer_count()
                    )
                    # The component windows rotate between scans. A bounded window
                    # is not the whole component graph, so successive scans on an
                    # unchanged board have to look at different components - the
                    # offset is persisted with the rest of the run state.
                    component_window = int(
                        self.state.metrics.get("component_window", 0)
                    )
                    scan = scan_net_pairs(
                        session, resolver=self.resolver, max_pairs=cfg.max_pairs,
                        max_per_net=cfg.max_per_net,
                        include_components=cfg.component_offers_per_net > 0,
                        max_components_per_net=cfg.components_per_net,
                        max_component_pairs_per_net=cfg.component_offers_per_net,
                        component_window=component_window,
                        include_substitutions=cfg.substitution_variants > 0,
                        max_substitutions_per_pair=cfg.substitution_variants,
                        fresh=(lambda candidate: not self.state.attempts.worked_on(
                            candidate, digest)) if cfg.coverage_first else None,
                        skip=lambda candidate: (
                            self.state.attempts.attempts_for_pair(candidate)
                            >= cfg.per_net_tries
                            and not self.state.attempts.stale_on(candidate, digest)
                        ) or self.state.attempts.exhausted_on(candidate, digest),
                    )
                    self.state.metrics["last_scan"] = scan.to_evidence()
                    self.state.metrics["component_window"] = component_window + 1
                    coverage = dict(scan.component_coverage or {})
                    omitted = int(
                        coverage.get("components_omitted_by_window", 0)
                    ) + int(coverage.get("pairs_omitted_by_cap", 0))
                    outstanding = int(engine.get_unrouted_count())
                    self.state.metrics["outstanding_edges"] = outstanding
                if outstanding == 0:
                    stop_reason = "no_outstanding_pairs"
                    break
                report.pairs_before = len(scan.pairs)
                if not scan.pairs:
                    # The board still has unrouted edges but this pass has no pair
                    # to offer (every candidate exhausted or unresolvable). That is
                    # "stopped", never "completed": completion is an authoritative
                    # connectivity result, not an empty filtered queue. When the
                    # component windows or their caps left part of the component
                    # graph unexamined, the stop is reported as *capped* - claiming
                    # global exhaustion from a fixed subset is exactly the claim
                    # this distinction exists to prevent.
                    stop_reason = ("pairs_exhausted" if omitted == 0
                                   else "pairs_exhausted_capped")
                    if omitted:
                        report.notes.append(
                            "component windows left part of the graph unexamined: "
                            f"{coverage.get('components_omitted_by_window', 0)} "
                            "component(s) outside this window, "
                            f"{coverage.get('pairs_omitted_by_cap', 0)} pair(s) "
                            "outside the per-net cap; the stop is capped, not global"
                        )
                    break

                pair = self._next_pair(scan.pairs, digest)
                if pair is None:
                    stop_reason = ("pairs_exhausted" if omitted == 0
                                   else "pairs_exhausted_capped")
                    break

                # The scan above is already spent; everything below mutates
                # copper. Refuse to start it when the run cannot also pay for the
                # acceptance DRC that has to follow, because a native call reaped
                # at its deadline takes the checkpoint-holding child with it and
                # leaves a rollback with nothing to verify against. Stopping here
                # is the same fail-closed answer without the quarantine.
                headroom_note = self._headroom_note()
                if headroom_note:
                    stop_reason = "time_limit_headroom"
                    report.notes.append(headroom_note)
                    break

                progress_tracker_before = self.state.progress.to_dict()
                record_start = len(self.state.attempts.records)
                attempt_started = cfg.wall_clock()
                # Leased end to end: every engine call the attempt makes — its
                # entry reads, its candidate sweep, its acceptance DRC and any
                # rollback — is bounded by the lease rather than by what is left of
                # the soft run budget.
                with self._transaction_lease():
                    progress, planner_used = self._attempt_pair(session, pair)
                attempt_seconds = max(0.0, cfg.wall_clock() - attempt_started)
                self.state.metrics["max_attempt_seconds"] = round(
                    max(
                        float(self.state.metrics.get("max_attempt_seconds", 0.0) or 0.0),
                        attempt_seconds,
                    ),
                    3,
                )
                iterations += 1
                self.state.metrics["iterations"] = iterations
                improved = self.state.progress.update(progress)
                report.model_usage = self._usage_snapshot()
                if self._stop_requested:
                    # A safety stop (unreadable state or a quarantine) outranks
                    # any progress bookkeeping: keep the last verified board and
                    # stop touching the engine.
                    stop_reason = self._stop_requested
                if improved and not self._stop_requested and not session.dirty:
                    try:
                        board_out, board_hash = self._save_best(session, progress)
                    except Exception as exc:  # noqa: BLE001 - promotion is fail-closed
                        self._stop_requested = "artifact_verification_failed"
                        # The observed candidate did not become the saved board.
                        # Restore the tracker snapshot from before this attempt;
                        # its transaction result remains in AttemptHistory below.
                        self.state.progress = ProgressTracker.from_dict(
                            progress_tracker_before
                        )
                        for item in self.state.attempts.records[record_start:]:
                            if item.committed:
                                item.final_disposition = "rejected_artifact_verification"
                        self.state.metrics.setdefault(
                            "rejected_candidate_history", []
                        ).extend(
                            item.to_dict() for item in
                            self.state.attempts.records[record_start:]
                            if item.final_disposition == "rejected_artifact_verification"
                        )
                        saved_progress = dict(self.state.best_progress or self._last_verified_progress)
                        self._last_verified_progress = dict(saved_progress)
                        self.state.metrics["artifact_verification_failure"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                        self.state.metrics["rejected_candidate_progress"] = dict(progress)
                        self.state.metrics["rejected_candidate_verification"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                        report.status = "blocked"
                        report.stop_reason = self._stop_requested
                        report.progress_after = dict(saved_progress)
                        report.notes.append(
                            "candidate was not promoted because saved-artifact "
                            "verification failed; the previous accepted generation remains active"
                        )
                        self.state.touch()
                        self.state.status = report.status
                        self.state.stop_reason = report.stop_reason
                        self.state.save()
                        break
                    self.state.best_board_path = board_out
                    self.state.best_board_sha256 = board_hash
                    self.state.best_progress = dict(progress)
                    report.best_board_path = board_out
                    report.best_board_sha256 = board_hash
                report.attempts = iterations
                report.plan_evaluations = len(self.state.attempts.records)
                report.accepted = sum(
                    1 for r in self.state.attempts.records
                    if r.accepted and r.final_disposition is None
                )
                report.progress_after = dict(progress)
                self.state.touch()
                self.state.metrics["elapsed_total_s"] = self._elapsed_s()
                self.state.save()
                if self._stop_requested:
                    break
                if self._soft_deadline_passed():
                    # The attempt crossed the run's soft deadline while it was in
                    # flight — its calls ran under a lease, so nothing was
                    # truncated and its rollback (if any) was verified. Stop here,
                    # at the transaction boundary, rather than letting the next
                    # dispatch be clamped and reaped.
                    stop_reason = "time_limit_completed_attempt"
                    report.notes.append(
                        "the attempt was allowed to finish past the run's time "
                        f"limit ({self._elapsed_s():.1f} s of "
                        f"{float(cfg.time_limit_s):.1f} s); stopping at this "
                        "transaction boundary instead of truncating its "
                        "verification"
                    )
                    break
                if cfg.progress_callback is not None:
                    cfg.progress_callback({
                        "attempts": report.attempts,
                        "accepted": report.accepted,
                        "progress": dict(progress),
                        "planner_used": planner_used,
                        "elapsed_s": round(self._elapsed_s(), 2),
                    })
                if self.state.progress.stalled:
                    stop_reason = "no_progress"
                    break

            # Final accounting: progress, usage, and the DRC delta on the kept board.
            report.status = "completed" if stop_reason == "no_outstanding_pairs" else "stopped"
            report.stop_reason = stop_reason
            if cfg.experimental_staging and report.status != "blocked":
                self.state.metrics["experimental_routing_stop_reason"] = stop_reason
                report.status = "experimental"
                report.stop_reason = "experimental_staging_only"
            if session.dirty or self._stop_requested:
                report.status = "blocked"
                stop_reason = self._stop_requested or "session_quarantined"
                report.stop_reason = stop_reason
                report.progress_after = dict(self._last_verified_progress)
                final_violations = None
                delta = None
            else:
                # The closing verification takes a lease of its own — this is the
                # second (and last) window that can lie past the soft deadline, and
                # the reason the run's overrun is bounded by two windows rather than
                # one. It is read-only and needs none of the scheduler's state, but
                # it is dispatched after the soft deadline whenever the run stopped
                # on that boundary, and under the plain clamp its allowance would be
                # zero: the child would be reaped and the run would end reporting an
                # engine exception instead of the DRC of the board it kept. That is
                # the failure the earlier segment of the campaign hit from
                # ``get_pad_groups``.
                with self._transaction_lease():
                    report.progress_after = self._safe_progress(session)
                    final_violations = take_violations(engine, rules_path)
                    delta = diff_sets(baseline, final_violations)
            report.drc = {
                "rules_path": rules_path,
                "baseline_relevant": baseline.relevant,
                "final_relevant": final_violations.relevant if final_violations else None,
                "added_relevant_count": len(delta.added_relevant) if delta else None,
                "resolved_count": len(delta.resolved) if delta else None,
                "added_relevant": delta.to_evidence()["added_relevant"] if delta else [],
                "resolved": delta.to_evidence()["resolved"] if delta else [],
                "connectivity_findings_before": baseline.connectivity,
                "connectivity_findings_after": (
                    final_violations.connectivity if final_violations else None
                ),
            }
            committed_attempts = [r for r in self.state.attempts.records if r.committed]
            if delta is not None and delta.added_relevant and committed_attempts:
                # The board on disk now carries more relevant findings than the
                # run's baseline even though every committed attempt was accepted
                # individually. That is not a result to promote: keep the last
                # verified checkpoint and say so.
                report.status = "blocked"
                report.stop_reason = "final_drc_regression"
                stop_reason = report.stop_reason
                self.state.metrics["final_drc_regression"] = {
                    "added_relevant": len(delta.added_relevant),
                    "samples": delta.to_evidence()["added_relevant"][:4],
                }
                report.notes.append(
                    "final native DRC reports "
                    f"{len(delta.added_relevant)} relevant finding(s) the run did "
                    "not have at baseline; the previous verified checkpoint is kept "
                    "and this board is not promoted"
                )
                # A late final DRC result can invalidate the just-promoted run
                # generation. Restore the pointer that was accepted before this
                # run started; immutable generations remain for diagnosis.
                artifact_store.restore_pointer(accepted_at_run_start)
                prior = artifact_store.current()
                if prior is not None:
                    prior_dir, prior_manifest = prior
                    self.state.best_board_path = os.path.join(
                        prior_dir, "board.kicad_pcb"
                    )
                    self.state.best_board_sha256 = prior_manifest["files"]["board.kicad_pcb"]
                    self.state.best_progress = dict(prior_manifest.get("progress", {}))
                    self.state.metrics["artifact"] = prior_manifest
                    report.best_board_path = self.state.best_board_path
                    report.best_board_sha256 = self.state.best_board_sha256
                    report.progress_after = dict(self.state.best_progress)
                    # The pointer checkpoint is the source of tracker history;
                    # discard progress observations from copper that the final
                    # gate rejected, while preserving their attempt records.
                    tracker_payload = (
                        accepted_checkpoint.get("progress")
                        if isinstance(accepted_checkpoint, Mapping) else None
                    )
                    if tracker_payload:
                        self.state.progress = ProgressTracker.from_dict(tracker_payload)
                    else:
                        self.state.progress = ProgressTracker(patience=cfg.stall_patience)
                    self.state.progress.patience = cfg.stall_patience
                    self.state.progress.best = dict(self.state.best_progress)
                    rejected = []
                    for item in self.state.attempts.records[run_attempt_start:]:
                        if item.committed:
                            item.final_disposition = "rejected_final_drc_regression"
                            rejected.append(item.to_dict())
                    self.state.metrics.setdefault("rejected_candidate_history", []).extend(rejected)
                    self.state.metrics["rejected_candidate_progress"] = dict(
                        self._last_verified_progress
                    )
                    self.state.metrics["rejected_candidate_drc"] = dict(report.drc)
                    prior_attempts = (
                        accepted_checkpoint.get("attempts", [])
                        if isinstance(accepted_checkpoint, Mapping) else []
                    )
                    report.accepted = sum(
                        1 for item in prior_attempts
                        if bool(item.get("accepted"))
                        and not item.get("final_disposition")
                    )
                    # Report DRC for the board named by the restored pointer;
                    # retain the rejected candidate's DRC above for diagnosis.
                    report.drc = {
                        "subject": "accepted_artifact",
                        "artifact_generation": prior_manifest.get("generation"),
                        "rules_path": rules_path,
                        "baseline_relevant": baseline.relevant,
                        "final_relevant": baseline.relevant,
                        "added_relevant_count": 0,
                        "resolved_count": 0,
                        "added_relevant": [],
                        "resolved": [],
                        "connectivity_findings_before": baseline.connectivity,
                        "connectivity_findings_after": baseline.connectivity,
                    }
            # A DRC delta on a board nobody changed is engine jitter, not a
            # consequence of the run. The native DRC is not fully deterministic
            # on a large board (measured on the V3 pilot: 7930 and 7933 relevant
            # findings for the same file across separate engine loads), so a
            # report that showed only the counts would imply a change that did
            # not happen. Name it, and keep the numbers in the state metrics.
            if delta is not None and not committed_attempts and (delta.added_relevant or delta.resolved):
                report.notes.append(
                    "drc delta on an unmodified board: "
                    f"{len(delta.added_relevant)} added / {len(delta.resolved)} resolved "
                    "finding(s) with no committed copper change - the native DRC is "
                    "not fully deterministic at this board size, so read the delta as "
                    "engine jitter rather than as a result of the run"
                )
                self.state.metrics["drc_jitter_suspected"] = {
                    "added_relevant": len(delta.added_relevant),
                    "added_connectivity": len(delta.added_connectivity),
                    "resolved": len(delta.resolved),
                }
            report.mode_usage = self._mode_usage()
            self.state.metrics.update({
                "progress_final": report.progress_after,
                "drc": report.drc,
                "mode_usage": report.mode_usage,
                "elapsed_total_s": self._elapsed_s(),
            })
            if (
                self.state.best_board_path is None
                and report.stop_reason != "final_drc_regression"
                and not session.dirty
                and not self._stop_requested
            ):
                # Nothing was ever checkpointed (e.g. every attempt was refused):
                # still capture the board the run finished on, so the report has an
                # artifact and the state has a resume point.
                try:
                    board_out, board_hash = self._save_best(session, report.progress_after)
                except Exception as exc:  # noqa: BLE001 - initial baseline also gates
                    report.status = "blocked"
                    report.stop_reason = "artifact_verification_failed"
                    stop_reason = report.stop_reason
                    self.state.metrics["artifact_verification_failure"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
                    report.notes.append(
                        "the source or candidate board did not pass fresh saved-artifact "
                        "verification; no best artifact was accepted"
                    )
                else:
                    self.state.best_board_path = board_out
                    self.state.best_board_sha256 = board_hash
                    report.best_board_path = board_out
                    report.best_board_sha256 = board_hash
            self.state.status = report.status
            self.state.stop_reason = report.stop_reason
            report.run_state_path = self.state.save()
            report.timings = {"total_s": round(self._elapsed_s(), 2)}
            report.planner_categories = dict(
                self.state.model_usage.get("categories", {})
            )
            # The promoted artifact is reopened only after this run's engine has
            # closed (one live router per process), so hand the reopened reading
            # to ``finally``.
            self._reopen_progress = dict(report.progress_after)
            return report
        except Exception as exc:  # noqa: BLE001 - a crash must still checkpoint
            if self.state is not None:
                self.state.status = "stopped"
                self.state.stop_reason = f"exception:{type(exc).__name__}"
                try:
                    self.state.metrics["elapsed_total_s"] = self._elapsed_s()
                    self.state.save()
                except Exception:  # noqa: BLE001
                    pass
            raise
        finally:
            try:
                engine.close()
            except Exception:  # noqa: BLE001
                pass
            # One live router per process: the promoted artifact can only be
            # reopened once this run's engine is closed.
            try:
                confirmation = self._reopen_artifact(
                    getattr(self, "_reopen_progress", None)
                )
            except Exception as exc:  # noqa: BLE001 - reported, never fatal here
                confirmation = {"ok": False,
                                "reason": f"{type(exc).__name__}: {exc}"}
            self._artifact_confirmation = confirmation
            if self.state is not None:
                self.state.metrics["artifact_reopen"] = confirmation
                try:
                    self.state.save()
                except Exception:  # noqa: BLE001
                    pass
            if local_report is not None:
                # ``report`` is returned by reference, so the caller sees this.
                local_report.artifact_verification = dict(confirmation)
                if not confirmation.get("ok", True):
                    local_report.notes.append(
                        "the promoted artifact did not reopen as checkpointed: "
                        + str(confirmation.get("reason"))
                    )

    # -- per-pair attempt --------------------------------------------------

    #: Candidate kinds the violation-centred search produces. A pair whose
    #: history holds a recorded violation *and* still has one of these
    #: unevaluated is the most informed attempt available on the board.
    REFUSAL_KINDS: frozenset[str] = frozenset(
        {"drc_avoid", "drc_clear", "drc_escape", "drc_extent"})

    #: Refusal-derived family the *runner* produces with the session in hand: a
    #: free via spot is a property of the copper and cannot be reproduced from
    #: history for a selection decision, so its kind is compared against the
    #: pair's own records and against the run's own via-search evidence.
    #: (``via_jog`` is *not* here: the pure generator produces it from the hint
    #: alone, so it is already covered by the key comparison above.)
    SESSION_REFUSAL_KINDS: frozenset[str] = frozenset({"via_free"})

    def _refusal_unanswered(self, pair: NetPair) -> bool:
        """True when this pair's own recorded violation has no plan answered yet.

        Both halves matter. Without the hint there is nothing refusal-derived to
        try; with every refusal-derived plan already in ``tried_keys`` the pair
        has nothing new to offer and must not outrank a pair that does.

        The candidate list is computed with the *configured* budgets
        (``drc_candidate_limit``, ``drc_clearance_probes``, ``drc_clearance_mm``,
        ``drc_extent_probes``) rather than the generator's defaults: using the
        defaults made raising a limit change what the run evaluated without
        changing which pair it selected, which is exactly the phase-9 selection
        gap in a different disguise.
        """
        cfg = self.config
        if not cfg.coverage_first or int(cfg.drc_candidate_limit) <= 0:
            return False
        hints = self._pair_drc_hints(pair, cfg)
        if not hints:
            return False
        try:
            candidates = generate_candidates(
                pair,
                copper_layers=int(self._copper_layers_for_sort()),
                drc_hints=hints,
                max_drc_candidates=int(cfg.drc_candidate_limit),
                max_clearance_probes=int(cfg.drc_clearance_probes),
                drc_clearance_mm=float(cfg.drc_clearance_mm),
                max_extent_probes=int(cfg.drc_extent_probes),
            )
        except Exception:               # noqa: BLE001 - unreadable is not a plan
            return False
        tried = self.state.attempts.tried_keys(pair, self._pending_digest)
        if any(
            candidate.kind in self.REFUSAL_KINDS and candidate.key() not in tried
            for candidate in candidates
        ):
            return True
        # Two refusal-derived families cannot be reproduced here - the extent
        # family needs the obstacle observation and the hole family is computed
        # with the session - so they are measured by the records the pair already
        # has. A family that is switched on and has produced nothing for this pair
        # is work this pair still has coming; counting it as answered would put a
        # pair with an unevaluated family behind pairs with none, which is the
        # selection half of the phase-9 gap. An unproductive family is labelled
        # unknown, never impossible.
        hole_hint = any("hole" in str(hint[0]).lower() for hint in hints)
        seen_kinds = {str(record.kind) for record in self.state.attempts.for_pair(pair)}
        if (int(cfg.drc_extent_probes) > 0 and not hole_hint
                and "drc_extent" not in seen_kinds):
            return True
        # The hole family is measured two ways: a record from a plan it produced,
        # or the run's own search evidence - a search that ran and found nothing
        # is reported as an unproductive sample, so the family has been evaluated
        # even though no `via_free` candidate was ever executed.
        searched = bool(
            (self.state.metrics.get("via_search") or {}).get(json.dumps(pair.key))
        )
        if (hole_hint and cfg.via_search and not searched
                and not (seen_kinds & self.SESSION_REFUSAL_KINDS)):
            return True
        return False

    def _copper_layers_for_sort(self) -> int:
        """Copper layer count for a pure candidate-key computation, cached.

        Only used to decide *which family* a plan belongs to, never to place
        copper, so a conservative 2 is a safe answer when the count has not been
        read yet (the scan records the real one before any selection).
        """
        cached = getattr(self, "_copper_layers_cache", None)
        if cached is not None:
            return int(cached)
        return 2

    def _rule_minima(self, session: AgentSession) -> dict[str, float]:
        """The board's own minima the refusal-derived families measure against.

        The clearance search steps by *a* clearance and the extent family clears
        an obstacle by *a* clearance; both are guesses unless they use the rule
        the DRC will actually enforce. This reads the engine's own design rules
        once per run and keeps only values that are finite and non-negative - a
        negative field is KiCad's "unset" sentinel, and coercing one would put an
        unmeasurable distance into a plan. A missing or unusable value is simply
        absent, and the caller falls back to its configured default.
        """
        cached = getattr(self, "_rule_minima_cache", None)
        if cached is not None:
            return dict(cached)
        values: dict[str, float] = {}
        try:
            rules = session._engine.get_design_rules()
        except Exception:               # noqa: BLE001 - unreadable is not a rule
            rules = None
        for name, field_name in (("clearance_mm", "min_clearance_mm"),
                                 ("hole_clearance_mm", "min_hole_to_hole_mm")):
            raw = getattr(rules, field_name, None) if rules is not None else None
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            value = float(raw)
            if math.isfinite(value) and value >= 0.0:
                values[name] = value
        self._rule_minima_cache = values
        return dict(values)

    def _rule_clearance_mm(self, session: AgentSession) -> float | None:
        """The board's minimum clearance, or ``None`` when it cannot be trusted."""
        return self._rule_minima(session).get("clearance_mm")

    @staticmethod
    def _edge_from_pair_key(pair_key) -> tuple | None:
        """Canonical edge identity from a recorded ``[net, x0, y0, l0, x1, y1, l1]``."""
        if not pair_key or len(pair_key) != 7:
            return None
        try:
            return edge_key(
                (float(pair_key[1]), float(pair_key[2]), int(pair_key[3])),
                (float(pair_key[4]), float(pair_key[5]), int(pair_key[6])),
            )
        except (TypeError, ValueError):
            return None

    def _refusal_evidence(self, pair: NetPair, digest: str | None) -> tuple[bool, set[tuple]]:
        """Same-generation DRC-refusal evidence for this exact edge.

        Returns ``(the edge has refusal evidence on this digest, the plan keys
        the evidence names)``. Only records measured on the *current* board
        generation count: a refusal recorded against other copper is not evidence
        about this copper, so it is ignored rather than inherited. The edge is
        matched by its canonical key, through both the geometry actually
        attempted and the connection the attempt stood for, so a substitution
        offer still binds to the edge it was made for.
        """
        target = edge_key(pair.start, pair.target)
        edge_refused = False
        plans: set[tuple] = set()
        for record in self.state.attempts.records:
            if digest is not None and record.board_digest != digest:
                continue
            if record.reason != REASON_DRC_REGRESSION:
                continue
            edges = {
                self._edge_from_pair_key(key)
                for key in (record.pair_key, getattr(record, "offered_pair_key", None))
            }
            if target not in edges:
                continue
            edge_refused = True
            if record.plan_key:
                plans.add(attempt_plan_key(record.plan_key[0], record.plan_key[1]))
        return edge_refused, plans

    def _refusal_ranked(
        self, session: AgentSession, pair: NetPair, candidates: Sequence[Candidate],
        cfg: RunnerConfig,
    ) -> list[Candidate]:
        """Put measured openings ahead of the direct plans this copper refused.

        Opt-in and evidence driven. It fires only when all of these hold: the
        policy is enabled, the pair offers at least one ``zone_gap_via``
        candidate, the pair's *exact* edge carries same-generation DRC-refusal
        evidence, and that evidence names at least one of the direct candidates by
        plan key.

        The move is minimal, not a regroup: each measured opening that sits
        *after* the first already-refused direct candidate is moved to just in
        front of it, one at a time. So the non-refused direct plan, every other
        family and the order inside each group keep their positions, and the only
        thing that changes is the relative order of the openings and the plans the
        evidence says were already refused. That is enough for the sweep's own
        ``candidate_limit`` truncation to prefer a plan generated for the refusal
        over plans that refusal already rejected. Nothing is removed, and a clean
        connection never enters this path: with no evidence about this edge, the
        list is returned untouched.
        """
        if not cfg.zone_gap_via_priority or len(candidates) < 2:
            return list(candidates)
        openings = [candidate for candidate in candidates if candidate.kind == "zone_gap_via"]
        if not openings:
            return list(candidates)
        edge_refused, refused_plans = self._refusal_evidence(pair, self._pending_digest)
        if not edge_refused:
            return list(candidates)
        refused_direct = [
            candidate for candidate in candidates
            if candidate.kind in ("direct", "shove")
            and attempt_plan_key(candidate.mode, candidate.waypoints) in refused_plans
        ]
        if not refused_direct:
            return list(candidates)
        # Minimal, deterministic move: each measured opening that sits *after*
        # the first already-refused direct candidate is moved to just in front of
        # it, one at a time. Everything else - the non-refused direct plan, the
        # other families, the original order inside each group - keeps its
        # position, so the only thing this policy changes is the relative order of
        # the openings and the plans the evidence says were already refused.
        ordered = list(candidates)
        opening_ids = {id(candidate) for candidate in openings}
        refused_ids = {id(candidate) for candidate in refused_direct}
        moved = 0
        while True:
            first_refused = next(
                (index for index, candidate in enumerate(ordered)
                 if id(candidate) in refused_ids), None)
            if first_refused is None:
                break
            after = next(
                (index for index, candidate in enumerate(ordered)
                 if id(candidate) in opening_ids and index > first_refused), None)
            if after is None:
                break
            ordered.insert(first_refused, ordered.pop(after))
            moved += 1
        if not moved:
            return list(candidates)
        bucket = self.state.metrics.setdefault("zone_gap_via_priority", {})
        bucket["pairs_reordered"] = int(bucket.get("pairs_reordered", 0)) + 1
        bucket["directs_refused"] = (
            int(bucket.get("directs_refused", 0)) + len(refused_direct))
        bucket["openings_promoted"] = (
            int(bucket.get("openings_promoted", 0)) + moved)
        return ordered

    def _zone_coverage(self, session: AgentSession) -> zone_coverage.ZoneCoverage:
        """The run's zone-copper prefilter, rebuilt only when the fill changes.

        Zone fill polygons are not part of the per-attempt transactions: routing
        edits copper, it does not re-pour. So one prefilter per engine survives
        the whole run (and resumption), and a refill — the only thing that can
        change a fill answer — bumps ``zone_fill_epoch`` and replaces it.
        """
        engine = session._engine
        key = (id(engine), int(getattr(engine, "zone_fill_epoch", 0)))
        cached = self._zone_coverage_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        coverage = zone_coverage.ZoneCoverage(
            session, enabled=bool(self.config.zone_prefilter))
        self._zone_coverage_cache = (key, coverage)
        return coverage

    def _zone_margin_mm(self, session: AgentSession) -> tuple[float, str]:
        """The distance at which this board's own copper would touch a pour."""
        cached = getattr(self, "_zone_margin_cache", None)
        if cached is not None:
            return cached
        try:
            margin, basis = zone_coverage.board_margin_mm(session._engine)
        except Exception as exc:  # noqa: BLE001 - unreadable rules are "no margin"
            margin, basis = 0.0, f"rules unreadable ({type(exc).__name__})"
        self._zone_margin_cache = (float(margin), str(basis))
        return self._zone_margin_cache

    def _zone_openings(
        self, session: AgentSession, pair: NetPair, cfg: RunnerConfig,
    ) -> list[dict[str, Any]]:
        """Measured via openings for a cross-layer pair, cached like the others.

        The answer depends on zone fill, the pair's geometry and the observed
        obstacles, and changes with the copper digest — so it is cached on the
        same key the pour waypoints use and dropped when that changes.
        """
        if int(cfg.zone_gap_via) <= 0:
            return []
        if int(pair.start[2]) == int(pair.target[2]):
            return []                       # same-layer pair: nothing to transition to
        coverage = self._zone_coverage(session)
        if not coverage.available():
            return []
        margin, _basis = self._zone_margin_mm(session)
        cache_key = (pair.key, self._pending_digest, int(cfg.zone_gap_via))
        cached = self._zone_opening_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        try:
            obstacles = self._pair_obstacles(session, pair, cfg)
        except Exception:  # noqa: BLE001 - no observation is not a reason to guess
            obstacles = ()
        openings = zone_coverage.find_openings(
            coverage, pair.start, pair.target, pair.net_code,
            margin_mm=margin,
            search_mm=max(margin, float(cfg.zone_gap_via_search_mm)),
            pitch_mm=float(cfg.zone_gap_via_pitch_mm),
            max_samples=int(cfg.zone_gap_via_samples),
            max_openings=int(cfg.zone_gap_via),
            band_mm=float(cfg.zone_gap_via_band_mm),
            obstacles=obstacles,
        )
        rows = [opening.to_evidence() for opening in openings]
        bucket = self.state.metrics.setdefault("zone_gap_via", {})
        bucket["pairs_probed"] = int(bucket.get("pairs_probed", 0)) + 1
        bucket["openings_found"] = int(bucket.get("openings_found", 0)) + len(rows)
        bucket["margin_mm"] = round(float(margin), 4)
        if rows:
            bucket.setdefault("pairs", {})[json.dumps(list(pair.key))] = rows
        self._zone_opening_cache[cache_key] = list(rows)
        return list(rows)

    def _zone_ranked(
        self, session: AgentSession, pair: NetPair, candidates: Sequence[Candidate],
        cfg: RunnerConfig,
    ) -> list[Candidate]:
        """Move plans that touch a foreign pour behind the others, stably.

        Ranking, not filtering: the samples are the plan's *named geometry* — its
        waypoints plus a bounded sample of its own start->target corridor — and
        the router decides where copper actually lands, so no sample may decide
        that a plan is unwinnable.

        Two claims that look alike are not the same, and only the first holds:
        the returned list contains **every** candidate it was handed, and the
        sweep that consumes it truncates to ``candidate_limit`` *after* this
        ranking — so a plan moved late here can miss the attempt entirely. Plans
        the query cannot answer (no engine support, an unmapped layer, a covering
        zone with no stored fill) keep their existing position.
        """
        if not cfg.zone_prefilter or len(candidates) < 2:
            return list(candidates)
        coverage = self._zone_coverage(session)
        if not coverage.available():
            self._note_zone_evidence(
                pair, coverage, margin_mm=0.0, basis=coverage.unsupported_reason,
                checked=0, risky=0, unavailable=True)
            return list(candidates)

        margin, basis = self._zone_margin_mm(session)
        pitch = float(cfg.zone_prefilter_pitch_mm)
        samples = int(cfg.zone_prefilter_samples)
        corridor = ((pair.start, pair.target),)
        clean: list[Candidate] = []
        risky: list[Candidate] = []
        detail: list[dict[str, Any]] = []
        for candidate in candidates:
            risk = coverage.candidate_risk(
                candidate.waypoints, pair.net_code, margin_mm=margin,
                legs=corridor, pitch_mm=pitch, max_samples=samples)
            (risky if risk.is_foreign else clean).append(candidate)
            if risk.state != zone_coverage.NONE or risk.points:
                detail.append({"candidate": candidate.name, **risk.to_evidence()})

        self._note_zone_evidence(
            pair, coverage, margin_mm=margin, basis=basis,
            checked=len(candidates), risky=len(risky), unavailable=False,
            detail=detail)
        return clean + risky if risky else list(candidates)

    def _note_zone_evidence(
        self, pair: NetPair, coverage: zone_coverage.ZoneCoverage, *,
        margin_mm: float, basis: str, checked: int, risky: int,
        unavailable: bool, detail: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        """Record the prefilter's own view of this pair in the run metrics.

        Counts are run-level; the per-candidate detail is kept per pair so a
        report can show *why* a plan was ranked late without re-running the
        query. Nothing here changes an outcome — the state is evidence.
        """
        bucket = self.state.metrics.setdefault("zone_prefilter", {})
        bucket["enabled"] = bool(self.config.zone_prefilter)
        bucket["available"] = not unavailable
        bucket["margin_mm"] = round(float(margin_mm), 4)
        bucket["margin_basis"] = str(basis)
        bucket["pairs_ranked"] = int(bucket.get("pairs_ranked", 0)) + 1
        bucket["candidates_checked"] = int(bucket.get("candidates_checked", 0)) + int(checked)
        bucket["candidates_ranked_late"] = (
            int(bucket.get("candidates_ranked_late", 0)) + int(risky))
        bucket["stats"] = coverage.stats if not unavailable else {}
        if detail:
            pairs = bucket.setdefault("pairs", {})
            pairs[json.dumps(list(pair.key))] = list(detail)

    def _effective_rules_path(self, session: AgentSession) -> str:
        """The rule file the session's rule gate will use for validation."""
        if session.rule_context and session.rule_context.rules_path_exists:
            return session.rule_context.rules_path
        return self.config.rules_path or ""

    def _priority_edge_keys(self) -> frozenset[tuple]:
        """Normalized, direction-tolerant keys for the pinned exact edges."""
        keys: set[tuple] = set()
        for edge in self.config.priority_edges or ():
            try:
                x0, y0, l0, x1, y1, l1 = (float(edge[0]), float(edge[1]), int(edge[2]),
                                          float(edge[3]), float(edge[4]), int(edge[5]))
            except (TypeError, ValueError, IndexError):
                continue
            keys.add(edge_key((x0, y0, l0), (x1, y1, l1)))
        return frozenset(keys)

    @staticmethod
    def _pair_priority_edge(pair: NetPair, keys: frozenset) -> bool:
        """True when this pair *is* one of the pinned edges, either orientation."""
        if not keys:
            return False
        x0, y0, l0 = pair.start
        x1, y1, l1 = pair.target
        return edge_key((x0, y0, l0), (x1, y1, l1)) in keys

    def _next_pair(self, pairs: Sequence[NetPair], digest: str | None) -> NetPair | None:
        """First offered pair this run may still work on.

        ``scan_net_pairs`` already ordered the queue fairly (round-robin across
        nets, shortest first inside a net) and dropped pairs that are exhausted
        unless the copper around them has changed since they failed.
        """
        # The scan is fair within a scan, but repeatedly taking its first item
        # starves every later pair. Persist a cursor so capped representatives
        # are eventually attempted across loop iterations and resumes.
        cursor = int(self.state.metrics.get("pair_cursor", 0))
        if pairs:
            ordered = list(pairs[cursor % len(pairs):]) + list(pairs[:cursor % len(pairs)])
        else:
            ordered = []
        priority = {int(net) for net in self.config.priority_nets}
        if priority:
            ordered = (
                [pair for pair in ordered if pair.net_code in priority]
                + [pair for pair in ordered if pair.net_code not in priority]
            )
        # Exact-edge pins outrank net pins, and are applied after the rotation so
        # the cursor still rotates everything else.
        edge_priority = self._priority_edge_keys()
        if edge_priority:
            ordered = (
                [pair for pair in ordered if self._pair_priority_edge(pair, edge_priority)]
                + [pair for pair in ordered
                   if not self._pair_priority_edge(pair, edge_priority)]
            )
        if self.config.coverage_first:
            # Coverage/fairness: spend the next attempt on the outstanding pair
            # with the least history *on this board*, so a fixed budget reaches
            # the whole set instead of repeating one pair three times. The sort is
            # stable, so the cursor's rotation still decides between equals - a
            # resumed run does not pin itself to the same first pair.
            #
            # A pair whose own history holds an *unanswered* recorded refusal is
            # ahead of an untouched one. That is the selection half of the
            # violation-centred search: the generator can offer refusal-derived
            # plans, but if the selector never picks the pair they belong to they
            # are never evaluated - measured on the V3 campaign, 36 pairs carried
            # recorded violations and zero refusal-derived candidates were
            # evaluated across 1 318 s.
            ordered = sorted(
                ordered,
                key=lambda item: (
                    0 if edge_priority and self._pair_priority_edge(item, edge_priority) else
                    1 if priority and item.net_code in priority else 2,
                    0 if self._refusal_unanswered(item) else 1,
                    self.state.attempts.records_on(item, digest),
                ),
            )
        for pair in ordered:
            attempts = self.state.attempts.attempts_for_pair(pair)
            if attempts < self.config.per_net_tries:
                self.state.metrics["pair_cursor"] = cursor + 1
                return pair
            if self.state.attempts.stale_on(pair, digest):
                self.state.metrics["pair_cursor"] = cursor + 1
                return pair
        return None

    def _usage_snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.state.model_usage))

    def _mode_usage(self) -> dict[str, int]:
        """How often each routing mode was *used on copper that was kept*.

        Candidates a sweep applied and rolled back are evaluations, not uses: a
        six-candidate sweep would otherwise report six shove/detour "uses" for a
        pair that was never routed.
        """
        usage: dict[str, int] = {}
        for record in self.state.attempts.records:
            if record.probe_only or (record.sweep_member and not record.committed):
                continue
            usage[record.mode] = usage.get(record.mode, 0) + 1
        return dict(sorted(usage.items()))

    def _note_planner_category(self, category: str) -> None:
        categories = self.state.model_usage.setdefault("categories", {})
        categories[category] = int(categories.get(category, 0)) + 1

    def _attempt_pair(
        self, session: AgentSession, pair: NetPair
    ) -> tuple[dict[str, Any], bool]:
        """Try one connection: deterministic candidates first, then the planner."""
        cfg = self.config
        self._structural_refusal = ""
        offered = pair
        # Bind the offered-edge identity onto the pair itself. A mid-attempt
        # re-anchor replaces the anchors, and a substitution offer never had the
        # offered geometry in the first place, so every record below reads the
        # connection the scan actually drew from the pair rather than from the
        # geometry the attempt happened to use.
        pair = replace(pair, offered_key=offered_edge_key(offered))
        if self._stop_requested:
            return self._safe_progress(session), False
        if session.dirty:
            self._stop_requested = "session_quarantined"
            return self._safe_progress(session), False
        try:
            session_state = session.snapshot()
        except Exception:  # noqa: BLE001 - unreadable native state stops the run
            self._stop_requested = "session_unverified"
            return self._safe_progress(session), False
        if not session_state.verified:
            self._stop_requested = "session_unverified"
            return self._safe_progress(session), False
        if not pair.routable:
            # The anchors are the same point on the same layer: there is no copper
            # to add. Recorded with its reason so the taxonomy is explicit; this
            # is the *only* shape refused before trying, and it is refused because
            # there is nothing to route, not because the engine cannot do it.
            self.state.attempts.note(AttemptRecord(
                pair_key=pair.key, plan_key=(), candidate="unsupported_pair",
                mode="", kind="unsupported", source="deterministic",
                outcome="unsupported_pair", accepted=False, connected=False,
                reason=pair.pair_kind, duration_s=0.0,
                board_digest=self._pending_digest,
                offered_pair_key=offered_edge_key(pair),
            ))
            return self._safe_progress(session), False
        # Ratsnest identity is authoritative only while both resolved copper
        # clusters still carry the scheduled net. Refuse before planning or any
        # mutation if either endpoint now resolves to a different net.
        actual_nets = (
            session.net_at(*pair.start), session.net_at(*pair.target)
        )
        if session.dirty:
            self._stop_requested = "session_quarantined"
            return self._safe_progress(session), False
        anchor_evidence: dict[str, Any] = {}
        if any(net_code == 0 for net_code in actual_nets):
            # A ratsnest anchor can be a track-only cluster with no pad, so the
            # point-identity rule cannot name its net. That is a *scheduling*
            # gap, not an unroutable connection: re-anchor the endpoint on copper
            # the engine proves carries the scheduled net (a pad in the same
            # cluster, or a cluster whose every track/via agrees on it) and carry
            # on. The net is never guessed: an anchor that cannot be proved leaves
            # the pair retired for this copper generation.
            reanchored = reanchor_pair(session, pair, resolver=self.resolver)
            if reanchored is not None:
                pair, anchor_evidence = reanchored
                actual_nets = (
                    session.net_at(*pair.start), session.net_at(*pair.target)
                )
                if session.dirty:
                    self._stop_requested = "session_quarantined"
                    return self._safe_progress(session), False
                if pair.key != offered.key:
                    # The offered pair's endpoint was replaced by a proved
                    # alternative, so its plans belong to a different copper
                    # shape. Retire *this* offer for this copper generation and
                    # file the attempts against the pair actually attempted;
                    # without this the scan re-offers the same unusable anchor
                    # every iteration.
                    alternatives = anchor_evidence.get("component_candidates") or {}
                    self.state.attempts.note(AttemptRecord(
                        pair_key=offered.key, plan_key=(),
                        candidate="endpoint_identity", mode="",
                        kind="reanchored", source="deterministic",
                        outcome=AttemptHistory.EXHAUSTED_OUTCOME, accepted=False,
                        connected=False,
                        reason=(
                            "offered anchor replaced by a proved same-net "
                            "endpoint" if not alternatives else
                            "offered anchor carries no scheduled-net copper; "
                            "nearest proved same-net candidate offered instead"
                        ),
                        duration_s=0.0, board_digest=self._pending_digest,
                    ))
        if any(net_code == 0 for net_code in actual_nets):
            # Name the geometry: which layers carry copper here, what the nearest
            # scheduled-net item is, and what the nearest foreign item is. "No
            # anchor" is only actionable with the board objects behind it.
            diagnosis = []
            for name, point, observed in (("start", pair.start, actual_nets[0]),
                                          ("target", pair.target, actual_nets[1])):
                if observed != 0:
                    continue
                entry = anchor_diagnosis(session, point, int(pair.net_code),
                                         resolver=self.resolver)
                entry["endpoint"] = name
                diagnosis.append(entry)
            self.state.metrics.setdefault("unanchorable_anchors", {})[
                json.dumps(pair.key)] = diagnosis
            reason = (
                f"scheduled net {pair.net_code} endpoint identity is unsupported; "
                f"observed net codes {actual_nets}; "
                + "; ".join(entry["verdict"] for entry in diagnosis)
            )
            self.state.attempts.note(AttemptRecord(
                pair_key=pair.key, plan_key=(), candidate="endpoint_identity",
                mode="", kind="unsupported_pair", source="deterministic",
                outcome=AttemptHistory.EXHAUSTED_OUTCOME, accepted=False,
                connected=False, reason=reason, duration_s=0.0,
                board_digest=self._pending_digest,
                offered_pair_key=offered_edge_key(pair),
            ))
            return self._safe_progress(session), False
        if anchor_evidence:
            # The *offered* edge, not the substituted geometry: a re-anchor on a
            # substitution offer is one more step away from what the scan drew.
            anchor_evidence.setdefault("offered_pair_key",
                                       list(offered_edge_key(offered)))
            anchor_evidence.setdefault("attempted_pair_key", list(pair.key))
            self.state.metrics.setdefault("reanchored_pairs", {})[
                json.dumps(pair.key)] = anchor_evidence
        if actual_nets != (pair.net_code, pair.net_code):
            reason = (
                f"scheduled net {pair.net_code} endpoint identity changed: "
                f"observed {actual_nets}"
            )
            self._stop_requested = "endpoint_net_mismatch"
            self.state.attempts.note(AttemptRecord(
                pair_key=pair.key, plan_key=(), candidate="endpoint_identity",
                mode="", kind="safety_refusal", source="deterministic",
                outcome="endpoint_net_mismatch", accepted=False, connected=False,
                reason=reason, duration_s=0.0, board_digest=self._pending_digest,
                offered_pair_key=offered_edge_key(pair),
            ))
            return self._safe_progress(session), False
        tried = self.state.attempts.tried_keys(pair, self._pending_digest)
        rule_clearance = self._rule_clearance_mm(session)
        candidates = [
            candidate for candidate in generate_candidates(
                pair,
                board_bbox=self._board_bbox(session),
                copper_layers=int(session._engine.get_copper_layer_count()),
                obstacles=self._pair_obstacles(session, pair, cfg),
                pour_waypoints=self._pour_waypoints(session, pair, cfg),
                drc_hints=self._pair_drc_hints(pair, cfg),
                max_drc_candidates=cfg.drc_candidate_limit,
                drc_clearance_mm=float(cfg.drc_clearance_mm),
                max_clearance_probes=int(cfg.drc_clearance_probes),
                max_extent_probes=int(cfg.drc_extent_probes),
                rule_clearance_mm=rule_clearance,
                via_free_positions=self._via_free_positions(session, pair, cfg),
                zone_openings=self._zone_openings(session, pair, cfg),
            )
            if candidate.key() not in tried
        ]
        # Ranking, before the sweep is truncated: the router cannot see pour
        # copper at all, so a plan whose own waypoints sit in a foreign net's
        # pour is the shape the native gate refuses most often. Such plans keep
        # their transaction but move behind plans the prefilter cannot fault.
        # Foreign-pour ranking first (a heuristic), then the evidence-driven
        # refusal ordering (a fact about this copper), then the sweep's own
        # truncation - so a plan the evidence says is already refused cannot
        # displace the plan generated for that refusal.
        candidates = self._zone_ranked(session, pair, candidates, cfg)
        candidates = self._refusal_ranked(session, pair, candidates, cfg)[: cfg.candidate_limit]

        if candidates:
            # The whole candidate set is executed as real atomic transactions in
            # ranked order, stopping at the first accepted one. See
            # ``_apply_candidates`` for why this replaced a probe-then-apply round.
            accepted = self._apply_candidates(session, pair, candidates)
            if accepted is not None and accepted.get("accepted"):
                return self._safe_progress(session), False
            # A sweep that found nothing still leaves the planner entitled to a
            # try, exactly as the probe round did.

        planner_available = (
            cfg.planner is not None and cfg.max_model_requests > 0
            and int(self.state.model_usage.get("requests", 0)) < cfg.max_model_requests
        )
        if self._structural_refusal:
            # The engine cannot identify this pair's endpoint at all; no plan and
            # no model reply changes that, so the pair is retired with the reason.
            self.state.attempts.note(AttemptRecord(
                pair_key=pair.key, plan_key=(), candidate="schedule",
                mode="", kind="exhausted", source="deterministic",
                outcome=AttemptHistory.EXHAUSTED_OUTCOME, accepted=False,
                connected=False,
                reason=f"engine refused the endpoint: {self._structural_refusal}",
                duration_s=0.0, board_digest=self._pending_digest,
                offered_pair_key=offered_edge_key(pair),
            ))
            return self._safe_progress(session), False
        if not candidates and not planner_available:
            # Every deterministic plan for this pair has been evaluated and there
            # is no planner left to ask. Record it as exhausted so the queue moves
            # on instead of re-selecting the same dead pair until the stall timer
            # fires - which is how a run with routable pairs left ended at
            # "no_progress" without ever reaching them.
            self.state.attempts.note(AttemptRecord(
                pair_key=pair.key, plan_key=(), candidate="schedule",
                mode="", kind="exhausted", source="deterministic",
                outcome=AttemptHistory.EXHAUSTED_OUTCOME, accepted=False,
                connected=False,
                reason=(
                    "all deterministic plans evaluated and no planner budget left"
                    if cfg.planner is not None else
                    "all deterministic plans evaluated and no planner configured"
                ),
                duration_s=0.0, board_digest=self._pending_digest,
                offered_pair_key=offered_edge_key(pair),
            ))
            return self._safe_progress(session), False

        planner_result = self._try_planner(session, pair)
        return self._safe_progress(session), planner_result is not None

    def _pair_drc_hints(
        self, pair: NetPair, cfg: RunnerConfig,
    ) -> list[tuple[str, float, float, int]]:
        """Where the native gate refused this pair's earlier plans.

        A plan that closed the connection and was refused by the gate left the
        exact violation the refusal was about (class, position, layer). Feeding
        those back into candidate generation is what turns "try another sideways
        offset" into an alternative aimed at the obstruction. The hints are read
        from this pair's own history on the *current* board digest, so they
        describe the copper the next attempt will actually meet.
        """
        limit = max(0, int(cfg.drc_candidate_limit))
        if limit <= 0:
            return []
        # One hint per violation *class*, taken from the earliest record that
        # carries it. Feeding every new record's own geometry back in makes the
        # hint set grow and move with each attempt - and since the hint's position
        # is where that attempt's copper violated, the alternatives would chase a
        # moving target and never retire. The first position a class was refused
        # at is stable as the history grows, which is what makes the generated
        # plan keys stable too.
        first_per_class: dict[str, tuple[str, float, float, int]] = {}
        for record in self.state.attempts.for_pair(pair):
            if record.board_digest not in (None, getattr(self, "_pending_digest", None)):
                continue
            for hint in record.drc_hints:
                error_type = str(hint[0])
                if error_type in first_per_class:
                    continue
                first_per_class[error_type] = (
                    error_type, round(float(hint[1]), 4),
                    round(float(hint[2]), 4), int(hint[3]),
                )
        found = list(first_per_class.values())
        # Returned as a set-free, canonically ordered list: which alternatives are
        # generated must not depend on the order the pair's history happens to be
        # stored in, or the same copper would mint new plan keys each scan.
        return sorted(found, key=lambda item: (item[0], item[1], item[2], item[3]))

    def _hole_escape_seeds(
        self, session: AgentSession, pair: NetPair, cfg: RunnerConfig,
        hints: Sequence[tuple[str, float, float, int]],
    ) -> list[tuple[float, float]]:
        """Via-site seeds placed from the *actual* drilled geometry near a hole refusal.

        The via search's fixed anchors are the pair's endpoints, its midpoint and
        the positions the gate refused it at. That is enough to find *some* free
        spot, but it makes the search a grid around points that were chosen for
        other reasons. A refusal whose class names a hole already says where the
        hole field is, and the drilled items can be read: this places seeds at the
        distance a via needs from a hole - the hole's own radius, the via's
        radius and the board's hole-to-hole clearance - along the direction that
        keeps the route moving toward the far terminal.

        These are *seeds only*, and they stay a sample: the engine's
        ``pad_block_reason`` remains a prefilter and the native DRC remains the
        authority on whether a via may be kept. Nothing here decides that a spot
        is lawful, and an unproductive search is reported as a sample rather than
        as proof that no site exists.
        """
        budget = max(0, int(cfg.via_hole_seeds))
        if budget <= 0:
            return []
        refusal_points = [
            (float(hint[1]), float(hint[2])) for hint in hints
            if "hole" in str(hint[0]).lower()
        ]
        if not refusal_points:
            return []
        try:
            via_radius = float(session._engine.route_item_radius_mm(for_via=True))
        except Exception:               # noqa: BLE001 - a filter is not a plan
            return []
        clearance = cfg.via_hole_clearance_mm
        if clearance is None:
            clearance = self._rule_minima(session).get("hole_clearance_mm", 0.5)
        clearance = max(0.0, float(clearance))
        pitch = max(0.05, float(cfg.via_search_pitch_mm))
        # The drilled items the finding can be about: vias (drill known) and
        # through-hole pads (the pad's copper radius bounds the hole, and a via
        # placed outside the copper is outside the hole too). Copper-only pads are
        # not holes.
        holes: list[tuple[float, float, float]] = []
        try:
            for via in session._engine.get_vias():
                holes.append((float(via.x_mm), float(via.y_mm),
                              max(float(via.drill_mm), 0.05) / 2.0))
        except Exception:               # noqa: BLE001 - unreadable is not a hole
            pass
        try:
            for pad in session._engine.get_pads():
                if str(getattr(pad, "pad_type", "")) not in ("thru_hole", "np_thru_hole"):
                    continue
                holes.append((float(pad.x_mm), float(pad.y_mm),
                              max(float(pad.width_mm), float(pad.height_mm)) / 2.0))
        except Exception:               # noqa: BLE001 - unreadable is not a hole
            pass
        if not holes:
            return []
        seeds: list[tuple[float, float]] = []
        seen: set[tuple[float, float]] = set()
        directions = ((float(pair.target[0]), float(pair.target[1])),
                      (float(pair.start[0]), float(pair.start[1])))
        for hx, hy in refusal_points:
            near = sorted(
                (
                    (math.hypot(x_mm - hx, y_mm - hy), x_mm, y_mm, radius)
                    for x_mm, y_mm, radius in holes
                    if math.hypot(x_mm - hx, y_mm - hy) <= HOLE_SEED_RADIUS_MM
                ),
                key=lambda item: (item[0], item[1], item[2]),
            )
            for _distance, x_mm, y_mm, radius in near[:2]:
                for towards_x, towards_y in directions:
                    if len(seeds) >= budget:
                        break
                    dx, dy = towards_x - x_mm, towards_y - y_mm
                    norm = math.hypot(dx, dy)
                    if norm <= 1e-9:
                        continue
                    reach = radius + via_radius + clearance + pitch
                    seed = (round(x_mm + dx / norm * reach, 4),
                            round(y_mm + dy / norm * reach, 4))
                    if seed in seen:
                        continue
                    seen.add(seed)
                    seeds.append(seed)
        return seeds[:budget]

    def _via_free_positions(
        self, session: AgentSession, pair: NetPair, cfg: RunnerConfig,
    ) -> list[dict[str, Any]]:
        """Spots near this pair's anchors where a via is not pre-refused.

        The engine's ``pad_block_reason(for_via=True)`` answers a narrow question -
        whether the point lands on a through-hole pad, or grazes a same-net one -
        and nothing about tracks, zones, holes or clearance across the via's span.
        It is therefore used as a **prefilter**: a point it does not refuse is a
        candidate worth asking the DRC about, and the transaction's native
        acceptance remains the only authority on whether a via may be kept.

        A spot is only useful if the *continuation* from it can reach the target,
        so each accepted spot is asked the other question too: is the copper under
        it, on the escape layer, part of the target's own component? If it is, the
        connection closes when the via lands and the plan no longer depends on a
        line that may not exist. Those spots come first; prefilter-clean spots
        whose continuation is unproved are still offered after them, because a
        refusal to *prove* a continuation is not proof that none exists.

        Seeds are the pair's anchors, its midpoint, the positions the gate has
        already refused this pair at - a hole-clearance finding names where the
        hole field is, which is the neighbourhood worth searching outward from -
        and, for a hole-class refusal, the spots derived from the actual drilled
        geometry near the finding (see :meth:`_hole_escape_seeds`).

        The search is bounded on every axis - grid radius and pitch, accepted
        candidates, total probes, and a wall-clock deadline - and it abandons
        immediately when the run is stopping. It is a *sample*: the record says how
        much of each disc was probed, and an unproductive sample is reported as an
        unproductive sample, never as proof that no lawful site exists. Results are
        cached per (pair, board digest): the positions are a property of the
        copper, not of the attempt.
        """
        if not cfg.via_search or cfg.via_search_radius_mm <= 0:
            return []
        hints = self._pair_drc_hints(pair, cfg)
        hint_classes = {str(hint[0]).lower() for hint in hints}
        cross_layer = int(pair.start[2]) != int(pair.target[2])
        hole_refused = any("hole" in name for name in hint_classes)
        if not (cross_layer or hole_refused):
            return []
        key = (pair.key, self._pending_digest)
        cached = self._via_position_cache.get(key)
        if cached is not None:
            return cached

        engine = session._engine
        pitch = max(0.05, float(cfg.via_search_pitch_mm))
        radius = float(cfg.via_search_radius_mm)
        try:
            item_radius = float(engine.route_item_radius_mm(for_via=True))
        except Exception:               # noqa: BLE001 - a filter is not a plan
            self._via_position_cache[key] = []
            return []
        steps = int(radius / pitch)
        offsets = sorted(
            (
                (math.hypot(column * pitch, row * pitch), column * pitch, row * pitch)
                for column in range(-steps, steps + 1)
                for row in range(-steps, steps + 1)
                if not (column == 0 and row == 0)
                and math.hypot(column * pitch, row * pitch) <= radius + 1e-9
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )
        anchors: list[tuple[float, float]] = []

        def add_anchor(x_mm: float, y_mm: float) -> None:
            point = (round(float(x_mm), 4), round(float(y_mm), 4))
            if point not in anchors:
                anchors.append(point)

        # The target's own neighbourhood first: a spot there is the one most likely
        # to have the target's copper under it, and that is the spot whose via
        # closes the hop by itself.
        add_anchor(pair.target[0], pair.target[1])
        add_anchor((pair.start[0] + pair.target[0]) / 2.0,
                   (pair.start[1] + pair.target[1]) / 2.0)
        add_anchor(pair.start[0], pair.start[1])
        # Refusal geometry is a seed, not just an exclusion: the gate's own
        # findings say where the obstacles are.
        for hint in hints:
            hx, hy = float(hint[1]), float(hint[2])
            if abs(hx) > 1e-9 or abs(hy) > 1e-9:
                add_anchor(hx, hy)
        # Hole-derived seeds, from the drilled items the finding is about. These
        # are the anchors the search actually needs when the refusal is a hole
        # rule: a spot the right distance from *that* hole, on the way to the
        # target. They stay a seed set - the prefilter and the gate decide.
        hole_seeds = self._hole_escape_seeds(session, pair, cfg, hints)
        for seed_x, seed_y in hole_seeds:
            add_anchor(seed_x, seed_y)

        deadline = cfg.clock() + max(0.0, float(cfg.via_search_deadline_s))
        avoid_mm = max(0.0, float(cfg.via_search_avoid_mm))
        band_mm = max(0.05, float(cfg.via_search_band_mm))
        refused_at = [
            (float(hint[1]), float(hint[2])) for hint in hints
            if abs(float(hint[1])) > 1e-9 or abs(float(hint[2])) > 1e-9
        ]
        found: list[dict[str, Any]] = []
        verified: list[dict[str, Any]] = []
        bands_used: set[int] = set()
        probes = 0
        skipped_near_refusal = 0
        continuation_checks = 0
        truncated = ""
        # The layer the via would land on, and the layer the target's own copper
        # is on: for the continuation to close the hop they have to be the same.
        start_layer = int(pair.start[2])
        escape_layer = int(pair.target[2])
        if escape_layer == start_layer:
            escape_layer = start_layer + 1 if start_layer < 4 else start_layer - 1
        try:
            target_cluster = session.cluster(
                float(pair.target[0]), float(pair.target[1]), escape_layer,
            )
        except Exception:               # noqa: BLE001 - unreadable is not proof
            target_cluster = frozenset()
        # Share the candidate budget across the anchors instead of filling it from
        # the first one: the far side of a blocked hop is often near the *target*,
        # and a search that never reaches that anchor cannot find it.
        candidates_wanted = int(cfg.via_search_candidates)
        continuations_wanted = max(
            0, min(candidates_wanted, int(cfg.via_search_continuations))
        )
        unverified_wanted = max(0, candidates_wanted - continuations_wanted)
        quota = max(1, candidates_wanted // max(1, len(anchors)))
        extra = candidates_wanted % max(1, len(anchors))
        accepted_per_anchor: dict[tuple[float, float], int] = {
            anchor: 0 for anchor in anchors
        }
        # The probe budget is shared the same way. A wide radius means one anchor's
        # disc can be more probes than the whole budget, and letting it eat them all
        # would leave the other anchors - including the target's neighbourhood,
        # which is where a proved continuation usually is - never probed at all.
        probes_budget = int(cfg.via_search_probes)
        probes_per_anchor = max(1, probes_budget // max(1, len(anchors)))
        probes_leftover = probes_budget % max(1, len(anchors))
        probes_here = 0
        for anchor_x, anchor_y in anchors:
            anchor_index = anchors.index((anchor_x, anchor_y))
            allowed = quota + (1 if anchor_index < extra else 0)
            probes_allowed = probes_per_anchor + (1 if anchor_index < probes_leftover
                                                  else 0)
            probes_here = 0
            for distance, dx, dy in offsets:
                if (len(verified) >= continuations_wanted
                        and len(found) >= unverified_wanted):
                    break
                if probes >= int(cfg.via_search_probes):
                    truncated = "probe_budget"
                    break
                if probes_here >= probes_allowed:
                    break
                if self._stop_requested:
                    truncated = "run_stopping"
                    break
                if cfg.clock() > deadline:
                    truncated = "deadline"
                    break
                probes += 1
                probes_here += 1
                x_mm, y_mm = anchor_x + dx, anchor_y + dy
                if avoid_mm > 0 and any(
                    math.hypot(x_mm - rx, y_mm - ry) < avoid_mm
                    for rx, ry in refused_at
                ):
                    # A place the gate has already refused this pair at is not a
                    # candidate, however free the prefilter thinks it is.
                    skipped_near_refusal += 1
                    continue
                try:
                    blocked = engine.pad_block_reason(
                        x_mm, y_mm, item_radius_mm=item_radius, for_via=True,
                    )
                except Exception:       # noqa: BLE001 - unreadable is not a spot
                    continue
                if blocked:
                    continue
                continuation = False
                if target_cluster:
                    continuation_checks += 1
                    try:
                        probe_cluster = session.cluster(x_mm, y_mm, escape_layer)
                    except Exception:   # noqa: BLE001 - unreadable is not proof
                        probe_cluster = frozenset()
                    continuation = bool(probe_cluster & target_cluster)
                band = int(distance / band_mm)
                if continuation:
                    # A proved continuation is the point of the search: it takes a
                    # slot of its own, and the one-per-band spread does not apply
                    # (two proved spots beside each other are both useful).
                    if len(verified) >= continuations_wanted:
                        continue
                    bands_used.add((anchor_x, anchor_y, band))
                else:
                    if (len(found) >= unverified_wanted
                            or accepted_per_anchor[(anchor_x, anchor_y)] >= allowed):
                        # The unproved slots are full here: keep looking for a spot
                        # whose continuation *is* proved instead of spending the
                        # remaining budget on more of the same.
                        continue
                    if (anchor_x, anchor_y, band) in bands_used:
                        continue
                    bands_used.add((anchor_x, anchor_y, band))
                    accepted_per_anchor[(anchor_x, anchor_y)] += 1
                spot = {
                    "x_mm": round(x_mm, 4), "y_mm": round(y_mm, 4),
                    "distance_mm": round(distance, 4),
                    "band": band,
                    "anchor": [anchor_x, anchor_y],
                    "layer": escape_layer,
                    "continuation_proved": continuation,
                }
                if continuation:
                    verified.append(spot)
                else:
                    found.append(spot)
            if len(found) >= candidates_wanted:
                break
        # Continuation-proved spots first: those are the ones where the via itself
        # closes the hop. The rest are still offered - an unproved continuation is
        # not proof of an impossible one - but after the proved ones.
        ordered = (verified + found)[:candidates_wanted]
        evidence = {
            "positions": ordered, "probes": probes, "truncated": truncated,
            "radius_mm": radius, "pitch_mm": pitch,
            "skipped_near_refusal": skipped_near_refusal,
            "bands": sorted(bands_used),
            "continuation_proved": len(verified),
            "continuation_checks": continuation_checks,
            "anchors": [list(anchor) for anchor in anchors],
            # Which of the anchors came from the drilled geometry near a hole
            # refusal. Recorded so an unproductive search can be read as "these
            # seeds produced nothing" rather than "no lawful site exists".
            "hole_seeds": [list(seed) for seed in hole_seeds],
            "grid_points_per_anchor": len(offsets),
            "sampled": (
                "a bounded sample of each anchor's disc: an unproductive sample "
                "is reported as sampled, not as proof that no lawful site exists"
            ),
        }
        self.state.metrics.setdefault("via_search", {})[json.dumps(pair.key)] = evidence
        self._via_position_cache[key] = ordered
        return ordered

    def _pair_obstacles(
        self, session: AgentSession, pair: NetPair, cfg: RunnerConfig,
    ) -> list[dict[str, Any]]:
        """Foreign-net copper near the connection, for obstacle-derived plans.

        The observation is taken around the connection's midpoint with a radius
        that grows with the gap (bounded), so a long connection still sees the
        wall that blocks it without scanning the whole board for every attempt.
        """
        x = (pair.start[0] + pair.target[0]) / 2.0
        y = (pair.start[1] + pair.target[1]) / 2.0
        radius = min(max(float(pair.gap_mm) / 2.0, 3.0), 8.0)
        try:
            return nearest_obstacles(
                session, x, y, pair.net_code, resolver=self.resolver,
                radius_mm=radius, limit=cfg.obstacle_limit,
            )
        except Exception:  # noqa: BLE001 - an unreadable observation is not a plan
            return []

    def _pour_waypoints(
        self, session: AgentSession, pair: NetPair, cfg: RunnerConfig,
    ) -> list[dict[str, Any]]:
        """Verified points inside the far terminal's own copper component.

        Cached per (pair, board digest): the component membership is proved by the
        engine's connectivity, and re-proving it on an unchanged board would only
        spend native queries. A pour-aware plan targets copper that is *already
        connected to the far terminal*, so closing pad -> waypoint closes the
        connection - which is the only reason a waypoint inside a pour is useful.
        """
        key = (tuple(pair.key), self._pending_digest)
        cached = self._pour_cache.get(key)
        if cached is not None:
            return cached
        try:
            points = component_waypoints(
                session, pair, limit=cfg.pour_waypoints, samples=cfg.pour_samples,
                resolver=self.resolver,
            )
        except Exception:  # noqa: BLE001 - an unreadable component is not a plan
            points = []
        self._pour_cache[key] = points
        return points

    def _apply_candidates(
        self, session: AgentSession, pair: NetPair, candidates: Sequence[Candidate]
    ) -> Mapping[str, Any] | None:
        """Execute deterministic candidates in order, stopping at the first accepted.

        Each candidate is a real atomic transaction. A candidate that does not
        close the connection is rolled back *without* a native DRC - there is
        nothing to accept on a state that is being discarded, and the rollback is
        verified against the pre-transaction probe, which is the cheap rejection
        the profile justified. A candidate that does close the connection is
        judged by exactly one full native DRC on exactly the copper that would be
        kept; if it is refused, the transaction is rolled back and the next
        candidate runs on the restored board.

        This replaced a probe-then-apply round. The V3 profile (see
        ``tools/reliability/profile_attempt_cost.py``) measured a *whole-board*
        native DRC at ~39 s, and the probe round paid for three of them on a
        closure: one to accept the probe's copper, one for the follow-up apply's
        baseline (the probe's accepted set had just evicted the base baseline
        from the gate cache), and one for the apply's acceptance. Applying
        directly pays for the acceptance pass once, on the state that is actually
        kept.

        That acceptance pass is a whole-board DRC by default and the engine's
        scoped clearance re-check when the session was opened with
        ``incremental_drc=True`` (see ``DrcGate.verify``); either way it is one
        pass on the kept copper, and the promoted artifact is still re-verified
        by a fresh native child with a whole-board DRC before any promotion.

        A structural refusal is returned immediately: no other candidate geometry
        can fix an endpoint the engine cannot identify, so the caller retires the
        pair instead of spending an iteration per remaining candidate.
        """
        last: Mapping[str, Any] | None = None
        for candidate in candidates:
            if self._stop_requested:
                return last
            if self._soft_deadline_passed():
                # Every candidate is another transaction inside the attempt's
                # window, so a sweep that kept starting them would spend the window
                # on work the run has already decided not to keep. Stop at this
                # transaction boundary instead; the attempt that just finished is
                # already recorded.
                self._soft_deadline_reached = True
                break
            last = self._apply_candidate(session, pair, candidate, sweep_member=True)
            if session.dirty or self._stop_requested:
                return last
            reason = str((last.get("evidence") or {}).get("reason", ""))
            if reason in STRUCTURAL_REFUSALS:
                self._structural_refusal = reason
                return last
            if bool(last.get("accepted")):
                return last
        return last

    def _apply_candidate(
        self, session: AgentSession, pair: NetPair, candidate: Candidate,
        *, sweep_member: bool = False,
    ) -> Mapping[str, Any]:
        if self._stop_requested:
            return {}
        started = self.config.clock()
        snapshot = session.snapshot()
        if not snapshot.verified:
            self._stop_requested = "session_unverified"
            return {}
        # The whole transaction — steps, acceptance DRC and any rollback it owes —
        # runs under one lease, so none of those calls is truncated by the run's
        # soft scheduling deadline.
        with self._transaction_lease():
            result = session.connect_targets(
                pair.start, pair.target, candidate.mode,
                waypoints=candidate.waypoints, token=snapshot.token,
            )
        self._record(
            pair, candidate, result.to_dict(), source="deterministic",
            duration_s=self.config.clock() - started,
            sweep_member=sweep_member,
        )
        if session.dirty:
            # The session quarantined itself (a rollback it could not verify):
            # nothing further may be mutated through it.
            self._stop_requested = "session_quarantined"
        return result.to_dict()

    def _try_planner(
        self, session: AgentSession, pair: NetPair
    ) -> Mapping[str, Any] | None:
        """Ask the planner for one plan and execute it through the tool surface."""
        cfg = self.config
        planner = cfg.planner
        if self._stop_requested or session.dirty:
            if session.dirty and not self._stop_requested:
                self._stop_requested = "session_quarantined"
            return None
        if self._soft_deadline_reached or self._soft_deadline_passed():
            # A model request is not free and its plan would be another lease; do
            # not spend either once the run's scheduling budget is gone.
            self._soft_deadline_reached = True
            return None
        if planner is None or cfg.max_model_requests <= 0:
            return None
        requests_used = int(self.state.model_usage.get("requests", 0))
        if requests_used >= cfg.max_model_requests:
            self._note_planner_category(CATEGORY_CANCELLED)
            return None
        # Count real HTTP attempts, including retries, and keep the count stable
        # across a resume by remembering how many were already recorded.
        if "planner_request_offset" not in self.state.metrics:
            self.state.metrics["planner_request_offset"] = requests_used

        attempts = [record.to_dict() for record in self.state.attempts.for_pair(pair)]
        # The plan is only valid for the board the model actually saw. The digest
        # is compared after inference; a mismatch is a stale plan, not a licence
        # to mint a fresh token for copper the model never looked at.
        digest_at_observation = session.board_digest()
        observation = compact_observation(
            session, pair, resolver=self.resolver, attempts=attempts,
            radius_mm=cfg.observe_radius_mm, obstacle_limit=cfg.obstacle_limit,
        )
        if cfg.record_prompts:
            path = os.path.join(cfg.run_dir, "observations.jsonl")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(observation, sort_keys=True) + "\n")

        started = cfg.clock()
        set_budget = getattr(planner, "set_budget", None)
        if callable(set_budget):
            spent_tokens = (
                int(self.state.model_usage.get("prompt_tokens", 0))
                + int(self.state.model_usage.get("completion_tokens", 0))
            )
            set_budget(
                requests_remaining=max(
                    0, cfg.max_model_requests - int(self.state.model_usage.get("requests", 0))
                ),
                tokens_remaining=(
                    max(0, cfg.max_total_tokens - spent_tokens)
                    if cfg.max_total_tokens else None
                ),
                deadline=(
                    max(0.0, cfg.time_limit_s - self._elapsed_s())
                    if cfg.time_limit_s and self._run_started is not None else None
                ),
                clock=cfg.clock,
                on_request_reserved=self._reserve_planner_request,
            )
        try:
            reply = planner.propose(
                observation=observation, tool_schema=tool_schemas(),
                previous_attempts=attempts,
            )
        except PlannerError as exc:
            self._note_planner_category(exc.category)
            self._account_planner_requests(planner, requests_used)
            usage = exc.detail.get("usage") or {}
            self.state.model_usage["usage_unknown_requests"] = int(
                self.state.model_usage.get("usage_unknown_requests", 0)
            ) + int(exc.detail.get("usage_unknown_requests", 0) or 0)
            for key, field in (
                ("prompt_tokens", "prompt_tokens"),
                ("completion_tokens", "completion_tokens"),
                ("reasoning_tokens", "reasoning_tokens"),
                ("cached_tokens", "cached_prompt_tokens"),
            ):
                self.state.model_usage[field] = int(
                    self.state.model_usage.get(field, 0)
                ) + int(usage.get(key, 0) or 0)
            self.state.attempts.note(AttemptRecord(
                pair_key=pair.key, plan_key=(), candidate="planner_error",
                mode="", kind="planner", source="planner",
                outcome=f"planner_{exc.category}", accepted=False, connected=False,
                reason=str(exc), duration_s=cfg.clock() - started,
            ))
            return None

        self._account_planner_requests(planner, requests_used)
        self.state.model_usage["prompt_tokens"] = int(
            self.state.model_usage.get("prompt_tokens", 0)
        ) + int(reply.prompt_tokens)
        self.state.model_usage["completion_tokens"] = int(
            self.state.model_usage.get("completion_tokens", 0)
        ) + int(reply.completion_tokens)
        self.state.model_usage["reasoning_tokens"] = int(
            self.state.model_usage.get("reasoning_tokens", 0)
        ) + int(reply.reasoning_tokens)
        self.state.model_usage["cached_prompt_tokens"] = int(
            self.state.model_usage.get("cached_prompt_tokens", 0)
        ) + int(reply.cached_tokens)

        request = dict(reply.request)
        binding_error = self._bind_planner_request(request, pair)
        if binding_error:
            self.state.attempts.note(AttemptRecord(
                pair_key=pair.key, plan_key=(), candidate="planner",
                mode="", kind="planner", source="planner",
                outcome="planner_out_of_scope", accepted=False, connected=False,
                reason=binding_error, duration_s=cfg.clock() - started,
                board_digest=self._pending_digest,
            ))
            return None

        if digest_at_observation is not None:
            digest_now = session.board_digest()
            if digest_now != digest_at_observation:
                self.state.attempts.note(AttemptRecord(
                    pair_key=pair.key, plan_key=(), candidate="planner",
                    mode=str(request.get("mode", "")), kind="planner", source="planner",
                    outcome="planner_stale_state", accepted=False, connected=False,
                    reason=(
                        "the board changed between the observation and the reply; "
                        "the plan was computed against copper that no longer exists"
                    ),
                    duration_s=cfg.clock() - started,
                    board_digest=self._pending_digest,
                ))
                return None

        snapshot = session.snapshot()
        if not snapshot.verified:
            self._stop_requested = "session_unverified"
            return None
        request["token"] = snapshot.token          # the model never handles the raw token
        # Leased like a deterministic candidate: the planner's tool call is the
        # same atomic transaction through the same session.
        with self._transaction_lease():
            outcome = handle_request(session, request)
        if session.dirty:
            # A failed response does not imply a restored board. Quarantine
            # takes precedence over every planner return path, including a
            # refused tool outcome.
            self._stop_requested = "session_quarantined"
        result = outcome.get("result", {}) if isinstance(outcome, Mapping) else {}
        if not outcome.get("ok"):
            self.state.attempts.note(AttemptRecord(
                pair_key=pair.key, plan_key=(), candidate="planner",
                mode=str(request.get("mode", "")), kind="planner", source="planner",
                outcome="planner_refused", accepted=False, connected=False,
                reason=str(outcome.get("reason", "")), duration_s=cfg.clock() - started,
                board_digest=self._pending_digest,
            ))
            return None
        record = self._record(
            pair, None, result, source="planner",
            duration_s=cfg.clock() - started,
            plan_key=attempt_plan_key(
                str(request.get("mode", "")), request.get("waypoints") or ()
            ),
        )
        if session.dirty:
            self._stop_requested = "session_quarantined"
        return {"result": result, "record": record.to_dict()}

    def _reserve_planner_request(self) -> None:
        """Persist a spend reservation before an HTTP request leaves the host."""
        self.state.model_usage["requests"] = int(
            self.state.model_usage.get("requests", 0)
        ) + 1
        self.state.metrics["elapsed_total_s"] = self._elapsed_s()
        self.state.save()

    def _account_planner_requests(self, planner: Planner, requests_used: int) -> None:
        # The offset is the number of requests already persisted *before this
        # planner instance* started spending, so a resumed run keeps its totals.
        # Reading it back out of the state was the bug: the first run persisted
        # ``0`` and every later run then reported just its own calls.
        if self._planner_request_offset is None:
            self._planner_request_offset = int(requests_used)
            self.state.metrics["planner_request_offset"] = self._planner_request_offset
        offset = int(self._planner_request_offset)
        counted = getattr(planner, "request_count", None)
        if isinstance(counted, int):
            self.state.model_usage["requests"] = offset + counted
        else:
            self.state.model_usage["requests"] = requests_used + 1

    def _bind_planner_request(
        self, request: dict[str, Any], pair: NetPair
    ) -> str | None:
        """Refuse a planner reply that is not a plan for *this* connection.

        Returns a refusal reason, or ``None`` when the request is in scope. A
        model that answers a different question (a snapshot, a net selection,
        another pair, or a coordinate far from the requested anchors) has not
        planned this connection, and counting its result as a routing attempt
        would credit the agent with work it did not do.
        """
        tool = str(request.get("tool", ""))
        if tool != "connect_targets":
            return (
                f"planner replied with tool {tool!r}; only connect_targets plans "
                f"this connection ({pair.label()})"
            )
        tolerance_mm = 0.5
        for field, anchor in (("start", pair.start), ("target", pair.target)):
            raw = request.get(field)
            if raw is None:
                request[field] = list(anchor)
                continue
            if not isinstance(raw, (list, tuple)) or len(raw) not in (2, 3):
                return f"planner {field} {raw!r} is not a 2- or 3-value point"
            try:
                values = [float(value) for value in raw]
            except (TypeError, ValueError):
                return f"planner {field} {raw!r} has non-numeric coordinates"
            if (abs(values[0] - anchor[0]) > tolerance_mm
                    or abs(values[1] - anchor[1]) > tolerance_mm):
                return (
                    f"planner {field} {values[:2]} is not the requested anchor "
                    f"{list(anchor[:2])}"
                )
            request[field] = list(anchor)
        waypoints = request.get("waypoints") or ()
        if not isinstance(waypoints, (list, tuple)):
            return f"planner waypoints {waypoints!r} is not a list"
        for index, raw in enumerate(waypoints):
            if not isinstance(raw, (list, tuple)) or len(raw) not in (2, 3):
                return f"planner waypoint[{index}] {raw!r} is not a 2- or 3-value point"
            try:
                [float(value) for value in raw]
            except (TypeError, ValueError):
                return f"planner waypoint[{index}] {raw!r} has non-numeric coordinates"
        return None

    def _board_bbox(self, session: AgentSession) -> list[float] | None:
        try:
            bbox = session._engine.get_board_bbox()
            return [
                float(bbox.x_mm), float(bbox.y_mm),
                float(bbox.x_mm) + float(bbox.width_mm),
                float(bbox.y_mm) + float(bbox.height_mm),
            ]
        except Exception:  # noqa: BLE001 - no bbox means no clamping
            return None
