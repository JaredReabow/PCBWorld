"""Agent-facing tooling for the structured PCBWorld routing API.

This package is the *model plumbing* side of the integration: it renders the
tool schema and the compact observation for a provider, and executes validated
requests. The routing loop, ranking and safety policy live in
``pcb_world.agent.runner`` / ``pcb_world.agent.scheduler`` — one owner each.
"""

from methods.llm_agent.tools.pcbworld_tools import (
    REFUSAL_CATEGORIES,
    build_connection_prompt,
    describe_tools,
    execute_request,
)

__all__ = [
    "REFUSAL_CATEGORIES",
    "build_connection_prompt",
    "describe_tools",
    "execute_request",
]
