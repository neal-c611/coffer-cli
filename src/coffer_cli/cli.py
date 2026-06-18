"""CLI entry point.

v0.3.0 — the static `coffer scan` command has been removed. The
cost-review work moved to the `coffer-cost-review` npm package, which
ships a Claude Code skill that reads the codebase semantically. The
commands here are now:

  coffer install-skill   download the cost-review skill and copy it to
                         ~/.claude/skills/ (no Node required — fetches
                         the published npm tarball directly).
  coffer prices          show per-model pricing.
  coffer compare A B     per-call + monthly cost compare of two models.
  coffer version         print version.
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from coffer_cli import __version__
from coffer_cli._pricing import MODEL_PRICING, compute_cost

app = typer.Typer(
    name="coffer",
    help=(
        "Coffer CLI. As of v0.3, scanning lives in the coffer-cost-review "
        "Claude Code skill (install via npm or via `coffer install-skill`). "
        "This CLI now hosts a few small utilities — pricing lookup, model "
        "comparison, and the skill installer."
    ),
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

NPM_TARBALL_URL = "https://registry.npmjs.org/coffer-cost-review/-/coffer-cost-review-{version}.tgz"
SKILL_LATEST_VERSION = "0.1.0"  # bump when downstream npm package bumps


@app.command(name="install-skill")
def install_skill(
    target: Annotated[
        Path | None,
        typer.Option(
            "--target",
            help="Override install location. Defaults to ~/.claude/skills/",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite an existing skill of the same name."),
    ] = False,
    skill_version: Annotated[
        str,
        typer.Option(
            "--skill-version",
            help="npm package version of coffer-cost-review to fetch.",
        ),
    ] = SKILL_LATEST_VERSION,
) -> None:
    """Install the coffer-cost-review Claude Code skill from npm.

    No Node / npm required — fetches the npm tarball directly over HTTPS.
    """
    dest_root = target or (Path.home() / ".claude" / "skills")
    dest = dest_root / "coffer-cost-review"

    if dest.exists() and not force:
        console.print(
            f"[yellow]Skill already installed at {dest}[/yellow]\n"
            "Re-install with: [cyan]coffer install-skill --force[/cyan]"
        )
        raise typer.Exit(0)

    url = NPM_TARBALL_URL.format(version=skill_version)
    console.print(f"Downloading {url} ...")

    with tempfile.TemporaryDirectory() as tmp:
        tarball = Path(tmp) / "skill.tgz"
        try:
            urllib.request.urlretrieve(url, tarball)  # noqa: S310
        except Exception as exc:
            console.print(f"[red]Download failed:[/red] {exc}")
            raise typer.Exit(1) from exc

        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(tmp, filter="data")  # type: ignore[arg-type]

        skill_src = Path(tmp) / "package" / "skill"
        if not skill_src.exists():
            console.print(f"[red]Bundled skill not found in tarball at {skill_src}[/red]")
            raise typer.Exit(1)

        dest_root.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir()

        copied: list[str] = []
        for entry in skill_src.iterdir():
            if entry.is_file():
                shutil.copy2(entry, dest / entry.name)
                copied.append(entry.name)

    console.print(
        f"\n[green]✓ Installed skill to[/green] [cyan]{dest}[/cyan]\n"
        f"  Files: {', '.join(copied)}\n\n"
        "Open Claude Code and ask: [bold]'review my LLM costs'[/bold]\n"
        "\nLive runtime cost tracking: [cyan]https://cofferwise.com[/cyan]"
    )


@app.command(name="uninstall-skill")
def uninstall_skill(
    target: Annotated[
        Path | None,
        typer.Option(
            "--target",
            help="Override skill location. Defaults to ~/.claude/skills/",
        ),
    ] = None,
) -> None:
    """Remove the coffer-cost-review skill from ~/.claude/skills/."""
    dest_root = target or (Path.home() / ".claude" / "skills")
    dest = dest_root / "coffer-cost-review"
    if not dest.exists():
        console.print(f"[yellow]Skill not installed at {dest}[/yellow]")
        raise typer.Exit(0)
    shutil.rmtree(dest)
    console.print(f"[green]✓ Removed[/green] {dest}")


@app.command()
def prices() -> None:
    """Show the current per-model pricing table."""
    table = Table(title="Coffer model pricing (USD per 1M tokens)", title_style="bold")
    table.add_column("Provider", style="dim")
    table.add_column("Model", style="cyan")
    table.add_column("Input", justify="right")
    table.add_column("Cached input", justify="right")
    table.add_column("Output", justify="right")

    for model, p in MODEL_PRICING.items():
        table.add_row(
            p.provider,
            model,
            f"${p.input_per_million:.2f}",
            f"${p.cached_input_per_million:.2f}" if p.cached_input_per_million else "—",
            f"${p.output_per_million:.2f}",
        )
    console.print(table)


@app.command()
def compare(
    model_a: Annotated[str, typer.Argument(help="First model.")],
    model_b: Annotated[str, typer.Argument(help="Second model.")],
    input_tokens: Annotated[int, typer.Option(help="Input tokens per call.")] = 1000,
    output_tokens: Annotated[int, typer.Option(help="Output tokens per call.")] = 200,
    calls_per_day: Annotated[int, typer.Option(help="Calls per day.")] = 1000,
) -> None:
    """Compare two models' per-call and monthly cost at a given volume."""
    for m in (model_a, model_b):
        if m not in MODEL_PRICING:
            console.print(f"[red]Unknown model:[/red] {m}")
            raise typer.Exit(1)

    a = compute_cost(model=model_a, input_tokens=input_tokens, output_tokens=output_tokens)
    b = compute_cost(model=model_b, input_tokens=input_tokens, output_tokens=output_tokens)
    monthly_a = a * calls_per_day * 30
    monthly_b = b * calls_per_day * 30

    table = Table(title="Model cost comparison", title_style="bold")
    table.add_column("Model", style="cyan")
    table.add_column("Per call", justify="right")
    table.add_column(f"Monthly @ {calls_per_day:,}/day", justify="right", style="bold")
    table.add_row(model_a, f"${a:.6f}", f"${monthly_a:,.2f}")
    table.add_row(model_b, f"${b:.6f}", f"${monthly_b:,.2f}")
    console.print(table)

    if monthly_a > 0 and monthly_b != monthly_a:
        delta_pct = round((1 - monthly_b / monthly_a) * 100)
        if delta_pct > 0:
            console.print(
                f"\n[green]{model_b}[/green] is [bold]{delta_pct}%[/bold] cheaper "
                f"than [magenta]{model_a}[/magenta] at this volume."
            )
        else:
            console.print(
                f"\n[yellow]{model_b}[/yellow] is [bold]{-delta_pct}%[/bold] more "
                f"expensive than {model_a}."
            )


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"coffer-cli {__version__}  (skill: coffer-cost-review {SKILL_LATEST_VERSION})")


if __name__ == "__main__":
    app()
