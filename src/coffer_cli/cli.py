"""CLI entry point — scan / prices / compare."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from coffer_cli import __version__
from coffer_cli._pricing import MODEL_PRICING, compute_cost
from coffer_cli.patterns import Finding, find_patterns

app = typer.Typer(
    name="coffer",
    help="LLM cost utility. Scan code for cost-waste anti-patterns, "
    "look up model pricing, compare cost between models.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


_SEVERITY_STYLE = {
    "high": ("red", "🚨"),
    "medium": ("yellow", "🟡"),
    "low": ("blue", "🔵"),
}


@app.command()
def scan(
    path: Annotated[
        Path,
        typer.Argument(help="Directory or file to scan. Defaults to current directory."),
    ] = Path("."),
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit JSON for programmatic consumption (e.g. CI, Claude Code skill).",
        ),
    ] = False,
    severity: Annotated[
        str,
        typer.Option("--min-severity", help="Filter: high | medium | low"),
    ] = "low",
) -> None:
    """Find LLM cost-waste anti-patterns: retry storms, loops without batching,
    large uncached prompts, etc.

    We deliberately do NOT estimate dollar cost — static analysis can't know
    call volume. We find structural risks that founder review would have caught.
    """
    if not path.exists():
        console.print(f"[red]Path not found:[/red] {path}")
        raise typer.Exit(1)

    findings = find_patterns(path)

    threshold = {"high": 0, "medium": 1, "low": 2}.get(severity.lower(), 2)
    findings = [f for f in findings if {"high": 0, "medium": 1, "low": 2}[f.severity] <= threshold]

    if json_output:
        typer.echo(json.dumps([f.to_dict() for f in findings], indent=2))
        raise typer.Exit(0 if not findings else 0)

    _print_human(path, findings)
    if any(f.severity == "high" for f in findings):
        raise typer.Exit(1)  # non-zero for CI gating on HIGH


def _print_human(path: Path, findings: list[Finding]) -> None:
    console.print(f"\nScanning [cyan]{path.resolve()}[/cyan]...")

    if not findings:
        console.print("\n[green]✓ No cost-waste anti-patterns detected.[/green]\n")
        return

    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        counts[f.severity] += 1

    console.print(
        f"\nFound [bold]{len(findings)}[/bold] cost-risk findings: "
        f"[red]{counts['high']} high[/red] · "
        f"[yellow]{counts['medium']} medium[/yellow] · "
        f"[blue]{counts['low']} low[/blue]\n"
    )

    table = Table(show_lines=True)
    table.add_column("", width=3)
    table.add_column("Where", style="cyan", no_wrap=False)
    table.add_column("Pattern", style="magenta")
    table.add_column("Suggestion", style="white")

    for f in findings:
        color, emoji = _SEVERITY_STYLE[f.severity]
        table.add_row(
            f"[{color}]{emoji}[/{color}]",
            f"{f.path}:{f.line}\n[dim]{f.snippet}[/dim]",
            f.pattern,
            f.suggestion,
        )
    console.print(table)

    console.print(
        Panel(
            Text.from_markup(
                "[dim]Static analysis catches structural risks. For real per-feature "
                "and per-user cost in production, see "
                "[link=https://trycoffer.com]trycoffer.com[/link][/dim]"
            ),
            border_style="dim",
        )
    )


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
                f"\n[green]{model_b}[/green] is "
                f"[bold]{delta_pct}%[/bold] cheaper than [magenta]{model_a}[/magenta] "
                f"at this volume."
            )
        else:
            console.print(
                f"\n[yellow]{model_b}[/yellow] is "
                f"[bold]{-delta_pct}%[/bold] more expensive than {model_a}."
            )


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"coffer-cli {__version__}")


if __name__ == "__main__":
    app()
