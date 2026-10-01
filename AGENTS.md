# Jevex agent guide

## Purpose

Jevex selects a Codex model once per user turn with Jev and resumes the same Codex session. Keep Codex responsible for conversation history, tools, permissions, and compaction.

## Files

- `jevex.py`: Codex model discovery via app-server `model/list`, Jev typed choice, Codex `exec`/`exec resume`, local usage log, one-shot CLI.
- `jevex_tui.py`: Textual interface and event rendering. CSS is inline so the single-module wheel includes it.
- `test_jevex.py`: Small offline contract checks.
- `README.md`: User-facing contract and limitations.

## Working rules

1. Read the full path from prompt to catalog to Jev response to Codex resume before editing routing behavior.
2. Discover the visible model list from the installed Codex runtime. Do not maintain a parallel fixed model registry. Reopen the model picker once when new model IDs appear.
3. Validate Jev's choice against eligible models. On Jev failure, disclose the reason and use an eligible default. On catalog failure, stop.
4. Preserve the Codex session ID across user turns. Do not copy or rebuild its transcript in Jevex.
5. Load API keys from `JEV_API_KEY` or the mode-`0600` local key file. Never put real keys or user prompt bodies in logs, screenshots, tests, or commits.
6. Do not claim per-internal-call routing, account entitlement, quality gains, or quota savings without direct evidence.
7. Codex may report cumulative session usage. Subtract the previous session total before presenting per-turn cache numbers. A model switch and a cache change can correlate without proving causation or billed savings.
8. Run `python3 -m unittest -v` after code changes. For catalog changes, also inspect a live `model/list` result with the installed CLI. For TUI changes, run the headless mount test and inspect a screenshot.

## Boundaries

Prefer the official Codex CLI and app-server protocol over patches or account-token handling. Prefer Python stdlib for the route; Textual is the one UI dependency. Add a proxy only if a measured requirement cannot work through `codex exec resume`.
