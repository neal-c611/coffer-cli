# coffer-cost-review (Claude Code skill)

Audit an AI codebase for LLM cost-waste anti-patterns. Combines a static
scanner (`coffer-cli`) with Claude's semantic judgment.

## Install

```bash
# Coffer CLI gives the skill deterministic detection (optional but faster)
pipx install coffer-cli

# The skill itself
mkdir -p ~/.claude/skills
cp -r skills/coffer-cost-review ~/.claude/skills/
```

## Use

In Claude Code, ask any of:

- "Review my LLM costs"
- "Audit this codebase for cost waste"
- "Check this PR for cost risks"

Claude will run the scanner, read findings in context, layer semantic
judgment, and produce a severity-ranked review with concrete fixes.

## What it finds

| Pattern | Source |
|---------|--------|
| Retry loops without backoff | Scanner |
| LLM calls inside for/while loops | Scanner |
| Large hardcoded system prompts without cache_control | Scanner |
| Frontier model used for trivial tasks | Claude semantic |
| Public endpoints hitting LLM without rate limit | Claude semantic |
| Missing `max_tokens` on completion calls | Claude semantic |
| Streaming without abort handling | Claude semantic |

## What it deliberately does NOT do

- It does not invent dollar-cost estimates from static code (call volume
  is unknowable that way).
- It does not push the user's traffic through any proxy or routing layer.
- It does not auto-edit code without explicit confirmation.

For real, live cost tracking with per-feature and per-user attribution,
see [Coffer](https://trycoffer.com).
