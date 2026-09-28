# Phase 12 checkpoint

Astra accepted the session restart fix and the opt-in `zone_gap_via` family after review. The strict gate passed with 475 unit and 132 native tests, patch reproduction and separation passed, and `git diff --check` was clean. This accepts the harness behavior, not a V3 routing improvement.

The accepted V3 generation remains `1790471639479510000-6c4f8ab81b83-4969cfa5` with SHA256 `6c4f8ab81b83f2cb18044028c2a8252da595a57f85317a31c50e1f593477cf1b` and 135 unrouted. Phase 12 accepted zero connections. The matched experiment reached two of sixteen cross-layer refusal edges and found no measured openings. No board was promoted.

Next phase: select the sixteen refusal edges by exact edge identity, run a read-only opening scan over all sixteen first, and reserve matched transactional ON/OFF trials for edges with openings. Preserve the same accepted baseline, zero planner requests, bounded budgets and full promotion ladder. A net-only priority is insufficient because it selected same-layer pairs. Keep `zone_gap_via` off by default until a controlled trial shows value. Fix any edge selector in public generic code; keep board coordinates and per-edge evidence private.

Before any publication, decide whether board-specific rule values in older public reports also need redaction. Earlier coordinate examples were redacted in phase 12. No files are staged, committed or pushed; the work is local in the dirty shared tree.
