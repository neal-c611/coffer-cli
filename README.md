# coffer-cli

> ⚠️ **As of v0.3.0 the scanner has moved.** The static regex scanner this
> package shipped in v0.1.x – v0.2.0 produced too many false positives on
> real codebases (Aider, smolagents, crewAI, MetaGPT, OpenInterpreter all
> exposed limits in static analysis). The cost-review work now lives in
> a Claude Code skill that reads the codebase semantically:
>
> ```bash
> npm install -g coffer-cost-review
> ```
>
> If you can't (or won't) use npm, you can still use `pipx install
> coffer-cli` + `coffer install-skill` — that command now downloads the
> same skill files from the npm tarball.

## What `coffer-cli` 0.3 still does

- **`coffer install-skill`** — downloads the `coffer-cost-review` skill
  files from the npm tarball and copies them to `~/.claude/skills/`.
- **`coffer prices`** — show current per-model pricing for OpenAI /
  Anthropic / friends.
- **`coffer compare gpt-4o gpt-4o-mini`** — quick per-model cost
  comparison at a given volume.

That's it. It's a small Python utility now. The actual review happens
in Claude Code via the skill.

## What it doesn't do anymore

- ~~`coffer scan ./my-app`~~ — removed. Static regex was misclassifying
  framework patterns it couldn't see across files. The skill (which can
  read multiple files semantically) catches the same patterns properly.

## For runtime cost tracking

Static / semantic review tells you which patterns *might* be wasting
money. For per-feature / per-user / per-prompt **runtime** attribution,
see [Cofferwise](https://cofferwise.com).

## License

Apache 2.0.
