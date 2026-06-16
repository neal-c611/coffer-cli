"""Static detection of LLM cost-waste anti-patterns.

We aim for low false-positive rate over completeness. A finding should
be defensible: a reviewer who reads the snippet should agree it's a
real risk in most cases.

Detector catalog (by cost lever):

  Lever A — input tokens
    uncached_large_prompt          MED   Large hardcoded prompt without nearby cache_control
    dynamic_before_static_cache    HIGH  f-string interpolation in system message breaks auto-cache
    unbounded_conversation_history MED   `messages.append(...)` without truncation
  Lever B — output tokens
    missing_max_tokens             MED   LLM call without `max_tokens` cap
    reasoning_effort_high_default  MED   `reasoning_effort="high"` literal
  Lever C — price per token
    (semantic — handled in skill, not CLI)
  Lever D — number of calls
    llm_in_for_loop                MED   N× cost; Batch API / merged prompt are fixes
    agent_loop_no_max_iter         HIGH  `while True:` containing LLM call without iter cap
    temperature_nonzero_with_cache MED   `temperature > 0` next to a cache hint — silently breaks it
  Lever E — architecture / safety
    retry_loop_no_backoff          HIGH  Retry storm risk
    sdk_init_no_timeout            HIGH  SDK initialized without `timeout=`
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

Severity = Literal["high", "medium", "low"]

_LLM_CALL_RE = re.compile(
    r"""
    (
        \.chat\.completions\.create\(    |   # OpenAI sync
        \.completions\.create\(          |
        \.messages\.create\(             |   # Anthropic
        \.responses\.create\(            |   # OpenAI Responses API
        \.generate_content\(             |   # Google Gemini
        litellm\.(?:a)?completion\(      |
        ChatOpenAI\(                     |   # LangChain
        ChatAnthropic\(                  |
        Anthropic\(\)\.messages          |
        Anthropic\(\)\.completions
    )
    """,
    re.VERBOSE,
)

_RETRY_LOOP_RE = re.compile(
    r"""
    (
        for \s+ \w+ \s+ in \s+ range\(    |   # for attempt in range(...)
        while \s+ .* (?: retry | retries | attempts )   # while retries < N
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

_BACKOFF_RE = re.compile(
    r"""
    (
        backoff\.                          |   # backoff library
        @retry\(                           |   # tenacity / retrying
        tenacity\.                         |
        time\.sleep\(                      |
        asyncio\.sleep\(                   |
        2 \s* \*\* \s* attempt             |   # 2**attempt pattern
        2 \s* \*\* \s* retry
    )
    """,
    re.VERBOSE,
)

_FOR_LOOP_HEAD_RE = re.compile(r"^\s*(for|while)\s+.+:\s*$")
_INDENT_RE = re.compile(r"^( *|\t*)")

_CACHE_CONTROL_RE = re.compile(
    r"""
    (
        cache_control                |
        prompt_caching               |
        cache_key                    |
        @lru_cache                   |
        @cache
    )
    """,
    re.VERBOSE,
)

_PROMPT_VAR_HINT_RE = re.compile(
    r"^\s*(\w*(?:system|prompt|instruction|template)\w*)\s*=\s*",
    re.IGNORECASE,
)

# Match only variables that semantically represent the STATIC SYSTEM prefix.
# Anything user-side (user_prompt, user_message, ...) is inherently dynamic and
# is NOT a cache-break risk.
_FSTRING_PROMPT_RE = re.compile(
    r"""
    ^\s*
    (
        SYSTEM_PROMPT     |
        SYSTEM_MESSAGE    |
        SYSTEM_INSTRUCTION(S)?  |
        system_prompt     |
        system_message    |
        system_instruction(s)?  |
        sys_prompt        |
        SYS_PROMPT        |
        SYSTEM            |
        system            |
        INSTRUCTIONS?     |
        instructions?
    )
    \s* = \s*
    f ["']
    (?: [^"']|\\.)*?
    \{ [\w.\[\]'"]+ \}
    """,
    re.VERBOSE,
)

_AGENT_LOOP_HEAD_RE = re.compile(
    r"^\s*while\s+(?:True|not\s+\w+|1)\s*:\s*(?:#.*)?$"
)

_HISTORY_APPEND_RE = re.compile(
    r"^\s*(\w*(?:messages|history|conversation|chat)\w*)\.append\("
)

_HISTORY_TRUNCATE_RE = re.compile(
    r"""
    (
        \[\s*-?\d+\s*:\s*\]          |   # messages[-10:]
        \[\s*:\s*-?\d+\s*\]          |   # messages[:10]
        \.pop\(\s*0\s*\)              |
        \[\s*1:\s*\]                  |
        summari[sz]e_                 |
        compact_                      |
        truncate                      |
        trim_                         |
        memory\.add                   |
        mem0
    )
    """,
    re.VERBOSE,
)

_REASONING_EFFORT_HIGH_RE = re.compile(
    r"""reasoning_effort \s* = \s* ['"]high['"]""",
    re.VERBOSE,
)

_SDK_INIT_RE = re.compile(
    r"""
    \b
    (OpenAI | AsyncOpenAI | Anthropic | AsyncAnthropic)
    \(
    """,
    re.VERBOSE,
)

_TIMEOUT_KW_RE = re.compile(r"\btimeout\s*=")

_TEMPERATURE_RE = re.compile(r"\btemperature\s*=\s*([0-9]*\.?[0-9]+)")

_CACHE_HINT_NEARBY_RE = re.compile(
    r"""
    (
        @lru_cache              |
        @cache\b                |
        @cached\b               |
        functools\.cache        |
        \bcache\.get\(          |
        \bcache\.set\(          |
        \bredis\b               |
        \bmemcache\b            |
        diskcache\.             |
        cachetools\.            |
        TTLCache
    )
    """,
    re.VERBOSE,
)

_DEFAULT_INCLUDE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs")
_DEFAULT_SKIP_DIRS = frozenset(
    {
        ".git", ".venv", "venv", "node_modules", ".next", "dist", "build",
        "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
        ".turbo", "out", ".coffer-cache", "site-packages",
    }
)

# Minimum chars in a hardcoded string before we suspect "large uncached prompt".
_LARGE_PROMPT_THRESHOLD = 2_000


@dataclass(frozen=True)
class Finding:
    severity: Severity
    pattern: str
    path: Path
    line: int
    snippet: str
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "pattern": self.pattern,
            "file": str(self.path),
            "line": self.line,
            "snippet": self.snippet,
            "suggestion": self.suggestion,
        }


# ---- detectors --------------------------------------------------------------


def _detect_retry_loops(path: Path, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        if not _RETRY_LOOP_RE.search(line):
            continue
        # Find loop body indent
        indent_match = _INDENT_RE.match(line)
        loop_indent = len(indent_match.group(1)) if indent_match else 0

        body_lines: list[str] = []
        has_llm = False
        has_backoff = False

        for j in range(i + 1, min(i + 40, len(lines))):
            body = lines[j]
            if not body.strip():
                continue
            body_indent_match = _INDENT_RE.match(body)
            body_indent = len(body_indent_match.group(1)) if body_indent_match else 0
            if body_indent <= loop_indent:
                break
            body_lines.append(body)
            if _LLM_CALL_RE.search(body):
                has_llm = True
            if _BACKOFF_RE.search(body):
                has_backoff = True

        if has_llm and not has_backoff:
            findings.append(
                Finding(
                    severity="high",
                    pattern="retry_loop_no_backoff",
                    path=path,
                    line=i + 1,
                    snippet=line.strip()[:200],
                    suggestion=(
                        "Add exponential backoff (e.g. `@backoff.on_exception(backoff.expo, "
                        "RateLimitError, max_tries=5)`). A single rate-limit storm without "
                        "backoff can multiply your bill 10x."
                    ),
                )
            )
    return findings


def _detect_llm_in_loop(path: Path, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    loop_stack: list[tuple[int, int]] = []  # (line_idx, indent)

    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent_match = _INDENT_RE.match(line)
        cur_indent = len(indent_match.group(1)) if indent_match else 0

        # Pop loops whose body we exited
        while loop_stack and cur_indent <= loop_stack[-1][1]:
            loop_stack.pop()

        if _FOR_LOOP_HEAD_RE.match(line):
            loop_stack.append((i, cur_indent))
            continue

        if loop_stack and _LLM_CALL_RE.search(line):
            # Skip if this loop also looks like a retry loop — that's covered
            # by the retry detector with HIGH severity.
            loop_line = lines[loop_stack[-1][0]]
            if _RETRY_LOOP_RE.search(loop_line):
                continue
            findings.append(
                Finding(
                    severity="medium",
                    pattern="llm_in_for_loop",
                    path=path,
                    line=i + 1,
                    snippet=line.strip()[:200],
                    suggestion=(
                        "N LLM calls in a loop = N× token cost — asyncio.gather only fixes "
                        "latency, not the bill. Real cost fixes: (1) OpenAI Batch API for 50% off "
                        "on async workloads; (2) merge into one richer prompt that processes the "
                        "whole batch; (3) enable prompt caching if the system prompt repeats."
                    ),
                )
            )
    return findings


def _detect_large_uncached_prompts(path: Path, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _PROMPT_VAR_HINT_RE.match(line)
        if not match:
            i += 1
            continue

        # Look for triple-quoted string starting on this line or next
        joined: list[str] = []
        opener: str | None = None
        for q in ('"""', "'''"):
            if q in line[match.end():]:
                opener = q
                break
        if opener is None:
            i += 1
            continue

        # Collect until closing triple quote
        start_pos = line.find(opener, match.end())
        rest_of_start = line[start_pos + len(opener):]
        joined.append(rest_of_start)
        closed_on_start = opener in rest_of_start
        end_line = i

        if not closed_on_start:
            for j in range(i + 1, min(i + 200, len(lines))):
                joined.append(lines[j])
                if opener in lines[j]:
                    end_line = j
                    break
            else:
                i += 1
                continue
        else:
            end_line = i

        full = "\n".join(joined)
        # Remove the trailing opener piece
        if opener in full:
            full = full[: full.rfind(opener)]

        if len(full) < _LARGE_PROMPT_THRESHOLD:
            i = end_line + 1
            continue

        # Look in a window around the prompt for cache_control usage
        window_start = max(0, i - 30)
        window_end = min(len(lines), end_line + 30)
        window = "\n".join(lines[window_start:window_end])

        if _CACHE_CONTROL_RE.search(window):
            i = end_line + 1
            continue

        var_name = match.group(1)
        findings.append(
            Finding(
                severity="medium",
                pattern="uncached_large_prompt",
                path=path,
                line=i + 1,
                snippet=f"{var_name} = '''[{len(full):,} chars]'''",
                suggestion=(
                    "Large hardcoded prompt with no nearby cache_control. If called repeatedly, "
                    "wrap in Anthropic cache_control={'type': 'ephemeral'} or rely on OpenAI's "
                    "automatic caching to cut input cost 60-90%."
                ),
            )
        )
        i = end_line + 1
    return findings


def _detect_dynamic_before_static_cache_break(
    path: Path, lines: list[str]
) -> list[Finding]:
    """f-string interpolation in a system/instruction var — kills prefix caching.

    OpenAI auto-caches prefixes ≥1024 tokens. Anthropic uses cache_control
    on stable prefixes. Both break if the prompt starts with dynamic content.
    """
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        if not _FSTRING_PROMPT_RE.match(line):
            continue
        findings.append(
            Finding(
                severity="high",
                pattern="dynamic_before_static_cache_break",
                path=path,
                line=i + 1,
                snippet=line.strip()[:200],
                suggestion=(
                    "An f-string interpolation in this system/prompt variable defeats "
                    "automatic prefix caching (OpenAI auto-cache + Anthropic cache_control). "
                    "Restructure: put all dynamic content LAST (in messages[]), keep the static "
                    "prefix at the top. Or split: static system message + dynamic user message."
                ),
            )
        )
    return findings


def _detect_unbounded_conversation_history(
    path: Path, lines: list[str]
) -> list[Finding]:
    """`messages.append(...)` with no truncation/summarization in the file."""
    findings: list[Finding] = []
    appends: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        match = _HISTORY_APPEND_RE.match(line)
        if match:
            appends.append((i, match.group(1)))

    if not appends:
        return findings

    # Look at the whole file for any truncation/summarization indicator.
    full = "\n".join(lines)
    if _HISTORY_TRUNCATE_RE.search(full):
        return findings

    # One finding per file, at the first append.
    i, var = appends[0]
    findings.append(
        Finding(
            severity="medium",
            pattern="unbounded_conversation_history",
            path=path,
            line=i + 1,
            snippet=lines[i].strip()[:200],
            suggestion=(
                f"`{var}` grows without bound — every turn adds tokens permanently. "
                "Cap with sliding window (`messages = messages[-N:]`), summarize old turns "
                "(Mem0 / custom compaction), or use the provider's `previous_response_id` chain."
            ),
        )
    )
    return findings


def _detect_agent_loop_no_max_iter(path: Path, lines: list[str]) -> list[Finding]:
    """`while True:` containing an LLM call without a max-iteration counter.

    The canonical $47K-incident pattern. Detect:
      - `while True:` head
      - LLM call inside body
      - no `range(`/`max_iter`/`max_steps`/iteration counter pattern in body
    """
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        if not _AGENT_LOOP_HEAD_RE.match(line):
            continue
        indent_match = _INDENT_RE.match(line)
        loop_indent = len(indent_match.group(1)) if indent_match else 0

        body: list[str] = []
        for j in range(i + 1, min(i + 80, len(lines))):
            body_line = lines[j]
            if not body_line.strip():
                continue
            body_indent_match = _INDENT_RE.match(body_line)
            body_indent = len(body_indent_match.group(1)) if body_indent_match else 0
            if body_indent <= loop_indent:
                break
            body.append(body_line)

        body_text = "\n".join(body)
        if not _LLM_CALL_RE.search(body_text):
            continue
        # Heuristic for "has an iteration cap": find a counter pattern AND a break/return.
        has_counter = bool(
            re.search(
                r"""(\bmax_(?:iter|steps|turns|rounds)\b|\biter(?:ation)?s?\s*[+\-*/]?=|\bcount\s*[+\-*/]?=)""",
                body_text,
            )
        )
        has_break_or_return = bool(re.search(r"\b(break|return)\b", body_text))
        if has_counter and has_break_or_return:
            continue

        findings.append(
            Finding(
                severity="high",
                pattern="agent_loop_no_max_iter",
                path=path,
                line=i + 1,
                snippet=line.strip()[:200],
                suggestion=(
                    "Unbounded agent loop containing an LLM call. A single mis-firing tool "
                    "or model can spin forever — there is a documented $47K incident from this "
                    "exact pattern. Add `max_iter` counter and break, or use the provider's "
                    "explicit agent loop (OpenAI Responses with `max_tool_rounds`, "
                    "Anthropic tool_use with explicit termination check)."
                ),
            )
        )
    return findings


def _detect_temperature_nonzero_with_cache_hint(
    path: Path, lines: list[str]
) -> list[Finding]:
    """`temperature > 0` near a cache hint silently breaks the cache.

    Each call has different sampling → identical inputs produce different
    outputs → response cache misses every time. Developer thinks they're
    caching, but they're not.
    """
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        m = _TEMPERATURE_RE.search(line)
        if not m:
            continue
        try:
            if float(m.group(1)) <= 0:
                continue
        except ValueError:
            continue
        # Look 30 lines up and 30 lines down for a cache hint.
        window_start = max(0, i - 30)
        window_end = min(len(lines), i + 30)
        window = "\n".join(lines[window_start:window_end])
        if not _CACHE_HINT_NEARBY_RE.search(window):
            continue
        # If `temperature=0` is also seen in window, the user is mixing — still worth a hint
        findings.append(
            Finding(
                severity="medium",
                pattern="temperature_nonzero_with_cache_hint",
                path=path,
                line=i + 1,
                snippet=line.strip()[:200],
                suggestion=(
                    "A cache decorator/store is nearby, but this call sets `temperature > 0` — "
                    "sampling makes each response different, so the cache never hits on "
                    "subsequent identical inputs. Set `temperature=0` for cache-eligible "
                    "deterministic tasks, OR remove the cache layer if you genuinely need "
                    "varied outputs."
                ),
            )
        )
    return findings


def _detect_reasoning_effort_high_default(
    path: Path, lines: list[str]
) -> list[Finding]:
    """`reasoning_effort="high"` literal — usually a copy-paste from docs."""
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        if not _REASONING_EFFORT_HIGH_RE.search(line):
            continue
        findings.append(
            Finding(
                severity="medium",
                pattern="reasoning_effort_high_default",
                path=path,
                line=i + 1,
                snippet=line.strip()[:200],
                suggestion=(
                    "`reasoning_effort=\"high\"` is the new \"GPT-4 for everything\". On trivial "
                    "tasks it can produce ~20× extra reasoning tokens at full output price "
                    "(see arXiv 2412.21187). Default to `medium` or `low` and only escalate "
                    "for tasks that empirically need it."
                ),
            )
        )
    return findings


def _detect_sdk_init_no_timeout(path: Path, lines: list[str]) -> list[Finding]:
    """`OpenAI()` / `Anthropic()` constructed without `timeout=`."""
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        m = _SDK_INIT_RE.search(line)
        if not m:
            continue
        # Look at the next ~5 lines too in case the kwargs span lines.
        end = min(i + 5, len(lines))
        joined = "\n".join(lines[i:end])
        # Locate the close paren of this constructor.
        depth = 0
        start_pos = joined.index(m.group(0)) + len(m.group(0))
        body = ""
        for ch in joined[start_pos:]:
            body += ch
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1

        if _TIMEOUT_KW_RE.search(body):
            continue
        findings.append(
            Finding(
                severity="high",
                pattern="sdk_init_no_timeout",
                path=path,
                line=i + 1,
                snippet=line.strip()[:200],
                suggestion=(
                    f"`{m.group(1)}` initialized without `timeout=`. Default is 600s — a hung "
                    "provider can block your thread for ten minutes. Pass an explicit timeout "
                    "(e.g. `timeout=30.0`) sized to your user-facing latency budget."
                ),
            )
        )
    return findings


# ---- top-level --------------------------------------------------------------


def _walk_files(root: Path, suffixes: tuple[str, ...]) -> Iterable[Path]:
    if root.is_file():
        if root.suffix in suffixes:
            yield root
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in _DEFAULT_SKIP_DIRS for part in path.parts):
            continue
        yield path


def find_patterns(
    root: Path,
    suffixes: tuple[str, ...] = _DEFAULT_INCLUDE_SUFFIXES,
) -> list[Finding]:
    findings: list[Finding] = []
    for path in _walk_files(root, suffixes):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        findings.extend(_detect_retry_loops(path, lines))
        findings.extend(_detect_llm_in_loop(path, lines))
        findings.extend(_detect_large_uncached_prompts(path, lines))
        findings.extend(_detect_dynamic_before_static_cache_break(path, lines))
        findings.extend(_detect_unbounded_conversation_history(path, lines))
        findings.extend(_detect_agent_loop_no_max_iter(path, lines))
        findings.extend(_detect_temperature_nonzero_with_cache_hint(path, lines))
        findings.extend(_detect_reasoning_effort_high_default(path, lines))
        findings.extend(_detect_sdk_init_no_timeout(path, lines))

    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (severity_order[f.severity], str(f.path), f.line))
    return findings
