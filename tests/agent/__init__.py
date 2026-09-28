"""Tests for the agent reliability layer (``pcb_world.agent``).

Split deliberately:

* pure-Python unit tests (validation, snapshots, rule context, transaction
  bookkeeping against a test double) — no engine, no board;
* native tests that drive a real ``kicad_rl_router`` build over synthetic
  boards, and *skip loudly* when no build is present.
"""
