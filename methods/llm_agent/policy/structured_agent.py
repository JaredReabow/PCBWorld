"""Turn a repository LLM provider into a planner for the routing runner.

The routing loop lives in ``pcb_world.agent.runner``; this module only adapts the
repository's existing provider abstraction (:class:`KiCadLLMAgent`, which wraps
vLLM / API / remote providers) to the small :class:`Planner` protocol that loop
expects. No provider configuration is rewritten: construction still goes through
``KiCadLLMAgent.from_args`` so eval/CLI behaviour stays identical.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from methods.llm_agent.tools.pcbworld_tools import build_connection_prompt, describe_tools
from pcb_world.agent.runner import (
    CATEGORY_BAD_RESPONSE,
    CATEGORY_UNAVAILABLE,
    OpenAICompatiblePlanner,
    PlannerError,
    PlannerReply,
)


class ProviderPlanner:
    """Planner that asks a repository ``Provider`` for one JSON plan per call."""

    def __init__(
        self,
        provider: Any,
        *,
        name: str = "provider",
        temperature: float = 0.0,
        max_new_tokens: int = 700,
    ) -> None:
        self.provider = provider
        self.name = name
        self.temperature = temperature
        self.max_new_tokens = max_new_tokens

    def propose(
        self,
        *,
        observation: Mapping[str, Any],
        tool_schema: Mapping[str, Any],
        previous_attempts: Sequence[Mapping[str, Any]],
    ) -> PlannerReply:
        payload = dict(observation)
        if previous_attempts:
            payload["attempts"] = list(previous_attempts)
        system, user = build_connection_prompt(payload, tool_schema=tool_schema)
        try:
            texts, counts = self.provider.generate([(system, user)])
        except Exception as exc:  # noqa: BLE001 - classified for the runner
            raise PlannerError(
                f"provider call failed: {type(exc).__name__}: {exc}",
                category=CATEGORY_UNAVAILABLE,
            ) from None
        text = texts[0] if texts else ""
        prompt_tokens = completion_tokens = 0
        if counts:
            # TokenCounts mirrors may be tuples (system, user, output).
            try:
                entry = counts[0]
                if isinstance(entry, (tuple, list)) and len(entry) >= 3:
                    prompt_tokens = int(entry[0]) + int(entry[1])
                    completion_tokens = int(entry[2])
            except Exception:  # noqa: BLE001 - accounting is best effort
                prompt_tokens = completion_tokens = 0
        try:
            request = OpenAICompatiblePlanner.parse_reply(text)
        except PlannerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PlannerError(
                f"provider reply could not be parsed: {exc}",
                category=CATEGORY_BAD_RESPONSE,
            ) from None
        return PlannerReply(
            request=request, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, model=self.name, raw=text,
        )


def build_api_planner(
    *,
    api_provider: str,
    api_model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    max_new_tokens: int = 700,
) -> ProviderPlanner:
    """Planner backed by the repository's API provider table (openai/anthropic/...)."""
    from methods.llm_agent.policy.agent import KiCadLLMAgent

    args = SimpleNamespace(
        mode="api", api_provider=api_provider, api_model=api_model,
        api_key=api_key, temperature=temperature, max_new_tokens=max_new_tokens,
    )
    agent = KiCadLLMAgent.from_args(args, "api")
    return ProviderPlanner(
        agent, name=f"api:{api_provider}:{api_model or 'default'}",
        temperature=temperature, max_new_tokens=max_new_tokens,
    )


def build_remote_planner(
    *,
    remote_url: str,
    model: str,
    api_key: str | None = None,
    remote_no_auth: bool = False,
    temperature: float = 0.0,
    max_new_tokens: int = 700,
) -> ProviderPlanner:
    """Planner backed by the repository's remote (OpenAI-shaped server) provider."""
    from methods.llm_agent.policy.agent import KiCadLLMAgent

    args = SimpleNamespace(
        mode="llm", remote=True, remote_url=remote_url, model_path=model,
        api_model=model, api_key=api_key, remote_no_auth=remote_no_auth,
        temperature=temperature, max_new_tokens=max_new_tokens,
    )
    agent = KiCadLLMAgent.from_args(args, "llm")
    return ProviderPlanner(
        agent, name=f"remote:{model}", temperature=temperature,
        max_new_tokens=max_new_tokens,
    )
