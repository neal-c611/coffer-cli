# coffer-cli

> Scan your code for LLM cost-waste anti-patterns before you ship.

`coffer-cli` is a static scanner for production AI code. It catches the
mistakes that show up at month-end on your OpenAI / Anthropic bill —
retry storms, missing prompt caching, unbounded conversation history,
agent loops without iteration caps, SDK inits without timeouts, and
more.

It is intentionally **not** a magic dollar estimator. Static analysis
cannot know call volume; we leave that to live tracking. Instead, we
surface structural risks that a careful reviewer would catch — but
faster, in CI, on every commit.

```bash
pipx install coffer-cli

coffer scan ./my-app
coffer scan ./my-app --json     # for CI / Claude Code skill consumption
coffer prices                    # current model pricing table
coffer compare gpt-4o gpt-4o-mini
```

## What it catches (v0.1.0)

Detectors are organized by the four levers that drive LLM cost:

| Lever | Detector | Severity |
|-------|----------|----------|
| **A: input tokens** | `dynamic_before_static_cache_break` — f-string interpolation in `SYSTEM_PROMPT` defeats OpenAI auto-cache and Anthropic `cache_control` | 🚨 high |
| | `unbounded_conversation_history` — `messages.append(...)` without truncation or summarization | 🟡 med |
| | `uncached_large_prompt` — ≥2,000-char hardcoded prompt without nearby `cache_control` | 🟡 med |
| **B: output tokens** | `missing_max_tokens` — LLM call without a `max_tokens` cap | 🟡 med |
| | `reasoning_effort_high_default` — `reasoning_effort="high"` literal (up to ~20× extra reasoning tokens on trivial tasks) | 🟡 med |
| **D: number of calls** | `llm_in_for_loop` — N× cost; gather is a latency fix, not a cost fix | 🟡 med |
| | `agent_loop_no_max_iter` — `while True:` containing an LLM call without an iteration cap (the $47K-incident pattern) | 🚨 high |
| | `temperature_nonzero_with_cache_hint` — cache layer nearby but `temperature > 0` silently breaks it | 🟡 med |
| **E: architecture** | `retry_loop_no_backoff` — retry storm amplifies the bill 10× | 🚨 high |
| | `sdk_init_no_timeout` — default 600s lets a hung provider block your thread | 🚨 high |

Each finding includes a concrete fix and explains the *cost* angle
explicitly (we do not conflate latency fixes with cost fixes).

## Use with Claude Code (the skill)

The `coffer-cost-review` Claude Code skill in [`skills/`](skills/coffer-cost-review/)
combines this scanner with Claude's semantic judgment. In Claude Code, ask
*"review my LLM costs"* and the skill will:

1. Run `coffer scan <path> --json` for deterministic findings
2. Read each flagged file in context to filter false positives
3. Add semantic-only checks the scanner cannot do
   (frontier model used for trivial tasks, free-form output where structured
    works, public endpoints without rate limit, ...)
4. Produce a severity-ranked review with concrete code-diff fixes

Install:

```bash
git clone https://github.com/neal-c611/coffer-cli
mkdir -p ~/.claude/skills
cp -r coffer-cli/skills/coffer-cost-review ~/.claude/skills/
```

## What it deliberately does NOT do

- **No invented dollar estimates.** Call volume is unknowable from static
  code. We report severity, not numbers.
- **No proxy mode.** Your LLM calls go directly to your providers.
- **No auto-rewrites.** Suggestions only; you stay in control.

For live production cost tracking with per-feature and per-user attribution
(the part static analysis genuinely can't do), see
[Cofferwise](https://cofferwise.com).

## Exit codes

- `0` — clean, or only `medium`/`low` findings
- `1` — at least one `high` finding (use for CI gating)

## Development

```bash
git clone https://github.com/neal-c611/coffer-cli
cd coffer-cli
uv sync --extra dev
uv run pytest
```

Patterns are detected by `src/coffer_cli/patterns.py` (regex-based,
single-file scope) and rendered by `src/coffer_cli/cli.py` (typer +
rich).

Contributions welcome. New detectors should:

- Default to **medium** severity; reserve **high** for patterns that
  are demonstrably cost-amplifying in production
- Include a test in `tests/test_patterns.py` showing both a
  positive case AND a negative case (the negative case is what
  keeps false-positive rate low)
- Propose a *cost* fix, not a *latency* fix. Wrapping things in
  `asyncio.gather` does not reduce the bill.

## License

Apache 2.0.
