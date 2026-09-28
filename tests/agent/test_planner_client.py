"""Planner client safety and classification (no network: injected transport)."""

from __future__ import annotations

import json

import pytest

from pcb_world.agent.runner import (
    CATEGORY_AUTH,
    CATEGORY_BAD_RESPONSE,
    CATEGORY_RATE_LIMITED,
    CATEGORY_SERVER_ERROR,
    CATEGORY_TIMEOUT,
    CATEGORY_UNAVAILABLE,
    OpenAICompatiblePlanner,
    PlannerError,
    _SameHostRedirectHandler,
    resolve_api_key,
)


class _StubResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class _StubOpener:
    """Returns queued responses (or raises queued exceptions) and records requests."""

    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.requests: list = []

    def open(self, request, timeout=None):  # noqa: A003 - mirrors urlopen
        self.requests.append(request)
        item = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(item, Exception):
            raise item
        content, status, usage = item
        body = json.dumps({
            "choices": [{"message": {"content": content}}],
            "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7},
            "model": "stub-model",
        }).encode()
        return _StubResponse(body, status)


def _ok_payload(content: str):
    return (content, 200, None)


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def test_resolve_api_key_prefers_the_task_local_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    key_file = tmp_path / "task.key"
    key_file.write_text("sk-from-file\n")
    key, source = resolve_api_key(key_file=str(key_file))
    assert key == "sk-from-file"
    assert source == f"file:{key_file}"
    assert "sk-from-file" not in source          # the source is a location only


def test_resolve_api_key_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    key, source = resolve_api_key(key_file=str(tmp_path / "missing.key"))
    assert key == "sk-from-env"
    assert source == "env:DEEPSEEK_API_KEY"


def test_resolve_api_key_reports_absence_without_a_value(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    key, source = resolve_api_key(key_file=str(tmp_path / "missing.key"))
    assert key is None and source == ""


# ---------------------------------------------------------------------------
# Endpoint safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_url", [
    "http://api.deepseek.com",                       # not https
    "https://user:pass@api.deepseek.com",            # embedded credentials
    "https://api.deepseek.com?key=1",                # query string
    "https://api.deepseek.com#frag",                 # fragment
    "https://evil.example.com",                      # not allow-listed
])
def test_client_refuses_unsafe_endpoints(base_url):
    with pytest.raises(PlannerError) as exc:
        OpenAICompatiblePlanner(base_url=base_url, model="deepseek-flash")
    assert exc.value.category == CATEGORY_UNAVAILABLE


def test_client_allows_an_explicitly_authorised_host():
    planner = OpenAICompatiblePlanner(
        base_url="https://my-gateway.internal", model="local",
        allow_host="my-gateway.internal",
        opener=_StubOpener([_ok_payload('{"tool":"connect_targets"}')]),
    )
    assert planner.base_url == "https://my-gateway.internal"


def test_cross_host_redirects_are_refused():
    handler = _SameHostRedirectHandler()

    class _Request:
        full_url = "https://api.deepseek.com/chat/completions"

    with pytest.raises(PlannerError) as exc:
        handler.redirect_request(
            _Request(), None, 302, "Found", {}, "https://attacker.example/steal",
        )
    assert exc.value.category == CATEGORY_AUTH


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    '{"tool":"connect_targets","mode":"walkaround"}',
    '```json\n{"tool":"connect_targets"}\n```',
    'Here you go:\n{"tool":"connect_targets","mode":"shove"}\nThanks!',
    'We need one JSON object only.\n{"tool":"connect_targets"}\nDone.',
    '{"note":"thinking"}\n{"tool":"connect_targets","mode":"shove"}',
])
def test_parse_reply_accepts_json_in_common_shapes(text):
    payload = OpenAICompatiblePlanner.parse_reply(text)
    assert payload["tool"] == "connect_targets"


@pytest.mark.parametrize("text", ["no json here", "{broken", '{"mode":"walkaround"}'])
def test_parse_reply_rejects_malformed_replies(text):
    with pytest.raises(PlannerError) as exc:
        OpenAICompatiblePlanner.parse_reply(text)
    assert exc.value.category == CATEGORY_BAD_RESPONSE


# ---------------------------------------------------------------------------
# Calls: accounting, retries, classification, key handling
# ---------------------------------------------------------------------------


def _planner_with(opener, **kwargs) -> OpenAICompatiblePlanner:
    return OpenAICompatiblePlanner(
        base_url="https://api.deepseek.com", model="deepseek-flash",
        opener=opener, max_retries=kwargs.pop("max_retries", 3),
        api_key_file=kwargs.pop("api_key_file", None),
        **kwargs,
    )


def _key_file(tmp_path):
    path = tmp_path / "task.key"
    path.write_text("sk-super-secret-value")
    return path


def test_successful_call_reports_usage_and_never_leaks_the_key(tmp_path):
    opener = _StubOpener([_ok_payload('{"tool":"connect_targets","mode":"walkaround"}')])
    planner = _planner_with(opener, api_key_file=str(_key_file(tmp_path)))
    reply = planner.propose(observation={"connection": {}}, tool_schema={}, previous_attempts=[])

    assert reply.request["tool"] == "connect_targets"
    assert (reply.prompt_tokens, reply.completion_tokens) == (11, 7)
    assert planner.request_count == 1
    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.get_header("Authorization") == "Bearer sk-super-secret-value"
    assert "sk-super-secret-value" not in request.full_url
    assert "sk-super-secret-value" not in (request.data or b"").decode()
    # The accounting structure records the key *source*, never the key.
    assert planner.calls[0]["key_source"].startswith("file:")
    assert "sk-super-secret-value" not in json.dumps(planner.calls)


def test_bad_replies_are_retried_a_bounded_number_of_times(tmp_path):
    opener = _StubOpener([_ok_payload("definitely not json")])
    planner = _planner_with(opener, api_key_file=str(_key_file(tmp_path)), max_retries=3)
    with pytest.raises(PlannerError) as exc:
        planner.propose(observation={}, tool_schema={}, previous_attempts=[])
    assert exc.value.category == CATEGORY_BAD_RESPONSE
    assert planner.request_count == 3          # bounded, not unbounded


def test_rate_limit_is_retried_then_succeeds(tmp_path):
    class _RateLimitedOpener(_StubOpener):
        def open(self, request, timeout=None):  # noqa: A003
            self.requests.append(request)
            import urllib.error

            if len(self.requests) == 1:
                raise urllib.error.HTTPError(
                    request.full_url, 429, "Too Many Requests", {}, None
                )
            return _StubResponse(
                json.dumps({
                    "choices": [{"message": {"content": '{"tool":"connect_targets"}'}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }).encode()
            )

    planner = _planner_with(_RateLimitedOpener([]), api_key_file=str(_key_file(tmp_path)))
    # Keep the retry sleep short for the test.
    monkey_sleep = __import__("pcb_world.agent.runner", fromlist=["time"]).time.sleep
    try:
        __import__("pcb_world.agent.runner", fromlist=["time"]).time.sleep = lambda _s: None
        reply = planner.propose(observation={}, tool_schema={}, previous_attempts=[])
    finally:
        __import__("pcb_world.agent.runner", fromlist=["time"]).time.sleep = monkey_sleep
    assert reply.request["tool"] == "connect_targets"
    assert planner.request_count == 2


def test_auth_failure_is_not_retried(tmp_path):
    import urllib.error

    class _AuthFailOpener(_StubOpener):
        def open(self, request, timeout=None):  # noqa: A003
            self.requests.append(request)
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    planner = _planner_with(_AuthFailOpener([]), api_key_file=str(_key_file(tmp_path)))
    with pytest.raises(PlannerError) as exc:
        planner.propose(observation={}, tool_schema={}, previous_attempts=[])
    assert exc.value.category == CATEGORY_AUTH
    assert planner.request_count == 1


def test_server_error_is_retried_then_reported(tmp_path):
    import urllib.error

    class _ServerFailOpener(_StubOpener):
        def open(self, request, timeout=None):  # noqa: A003
            self.requests.append(request)
            raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)

    planner = _planner_with(
        _ServerFailOpener([]), api_key_file=str(_key_file(tmp_path)), max_retries=2,
    )
    runner_module = __import__("pcb_world.agent.runner", fromlist=["time"])
    sleeps: list[float] = []
    original = runner_module.time.sleep
    runner_module.time.sleep = lambda seconds: sleeps.append(seconds)
    try:
        with pytest.raises(PlannerError) as exc:
            planner.propose(observation={}, tool_schema={}, previous_attempts=[])
    finally:
        runner_module.time.sleep = original
    assert exc.value.category == CATEGORY_SERVER_ERROR
    assert planner.request_count == 2
    assert sleeps                                # it actually backed off


def test_missing_key_is_an_auth_error_without_a_call(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    opener = _StubOpener([_ok_payload('{"tool":"connect_targets"}')])
    planner = _planner_with(opener, api_key_file=str(tmp_path / "nope.key"))
    with pytest.raises(PlannerError) as exc:
        planner.propose(observation={}, tool_schema={}, previous_attempts=[])
    assert exc.value.category == CATEGORY_AUTH
    assert opener.requests == []


def test_reasoning_content_is_reported_but_never_executed(tmp_path):
    """`reasoning_content` is the model thinking, not a plan to run.

    Running a scratchpad as tool input would execute text the model never
    committed to as an answer, so an empty `content` is a bad reply - with the
    reasoning tokens still reported, because they were spent.
    """
    class _ReasoningOpener(_StubOpener):
        def open(self, request, timeout=None):  # noqa: A003
            self.requests.append(request)
            body = json.dumps({
                "choices": [{
                    "message": {"content": "", "reasoning_content": '{"tool":"connect_targets"}'},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 40, "completion_tokens": 23,
                    "completion_tokens_details": {"reasoning_tokens": 16},
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            }).encode()
            return _StubResponse(body)

    planner = _planner_with(
        _ReasoningOpener([]), api_key_file=str(_key_file(tmp_path)), max_retries=1,
    )
    with pytest.raises(PlannerError) as exc:
        planner.propose(observation={}, tool_schema={}, previous_attempts=[])
    assert exc.value.category == CATEGORY_BAD_RESPONSE
    # The thinking tokens were spent and are reported rather than dropped.
    assert exc.value.detail["usage"]["reasoning_tokens"] == 16


def test_empty_reply_after_a_token_limit_escalates_once_then_fails(tmp_path):
    """A truncated reply is retried with a bigger budget, never re-sent as-is.

    Re-sending an identical under-budget request burns a paid request for the
    same result; the client escalates the output allowance once and then stops
    with the spend reported.
    """
    class _TruncatedOpener(_StubOpener):
        def open(self, request, timeout=None):  # noqa: A003
            self.requests.append(json.loads(request.data.decode()))
            body = json.dumps({
                "choices": [{"message": {"content": "", "reasoning_content": ""},
                             "finish_reason": "length"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 200},
            }).encode()
            return _StubResponse(body)

    opener = _TruncatedOpener([])
    planner = _planner_with(
        opener, api_key_file=str(_key_file(tmp_path)), max_retries=3,
    )
    runner_module = __import__("pcb_world.agent.runner", fromlist=["time"])
    original = runner_module.time.sleep
    runner_module.time.sleep = lambda _s: None
    try:
        with pytest.raises(PlannerError) as exc:
            planner.propose(observation={}, tool_schema={}, previous_attempts=[])
    finally:
        runner_module.time.sleep = original
    assert exc.value.category == CATEGORY_BAD_RESPONSE
    assert exc.value.retryable is False
    assert "max_tokens" in str(exc.value)
    # Exactly one escalation: the second request asks for more, and the third is
    # never sent because the same truncation is not retried again.
    assert len(opener.requests) == 2
    assert opener.requests[1]["max_tokens"] > opener.requests[0]["max_tokens"]
    assert exc.value.detail["usage"]["completion_tokens"] == 400
