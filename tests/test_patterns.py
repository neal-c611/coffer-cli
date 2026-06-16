"""Pattern detector tests."""

from __future__ import annotations

from pathlib import Path

from coffer_cli.patterns import find_patterns


def _write(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text(body)
    return p


def test_retry_loop_without_backoff_high_severity(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "chat.py",
        (
            "def call_with_retry(messages):\n"
            "    for attempt in range(10):\n"
            "        response = client.chat.completions.create(model='gpt-4o-mini', messages=messages)\n"
            "        return response\n"
        ),
    )
    findings = find_patterns(tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.pattern == "retry_loop_no_backoff"
    assert f.severity == "high"


def test_retry_loop_with_backoff_is_ignored(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "chat.py",
        (
            "import backoff\n"
            "def call_with_retry(messages):\n"
            "    for attempt in range(10):\n"
            "        backoff.expo(attempt)\n"
            "        return client.chat.completions.create(model='gpt-4o-mini', messages=messages)\n"
        ),
    )
    findings = find_patterns(tmp_path)
    assert findings == []


def test_llm_in_for_loop_medium(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "batch.py",
        (
            "def process(items):\n"
            "    for item in items:\n"
            "        client.chat.completions.create(model='gpt-4o-mini', messages=[item])\n"
        ),
    )
    findings = find_patterns(tmp_path)
    assert len(findings) == 1
    assert findings[0].pattern == "llm_in_for_loop"
    assert findings[0].severity == "medium"


def test_retry_pattern_does_not_double_report_as_for_loop(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "chat.py",
        (
            "for attempt in range(5):\n"
            "    client.chat.completions.create(model='gpt-4o-mini', messages=[])\n"
        ),
    )
    findings = find_patterns(tmp_path)
    # Should only flag retry_loop_no_backoff, NOT also llm_in_for_loop
    assert len(findings) == 1
    assert findings[0].pattern == "retry_loop_no_backoff"


def test_large_uncached_prompt_medium(tmp_path: Path) -> None:
    body = "x" * 2500
    _write(
        tmp_path,
        "agent.py",
        (
            f'SYSTEM_PROMPT = """{body}"""\n'
            "\n"
            "def chat(msg):\n"
            "    client.messages.create(model='claude-3-5-sonnet-20241022',\n"
            "                            system=SYSTEM_PROMPT, messages=[msg])\n"
        ),
    )
    findings = find_patterns(tmp_path)
    patterns = {f.pattern for f in findings}
    assert "uncached_large_prompt" in patterns


def test_large_prompt_with_cache_control_is_ignored(tmp_path: Path) -> None:
    body = "x" * 2500
    _write(
        tmp_path,
        "agent.py",
        (
            f'SYSTEM_PROMPT = """{body}"""\n'
            "\n"
            "def chat(msg):\n"
            "    client.messages.create(\n"
            "        model='claude-3-5-sonnet-20241022',\n"
            "        system=[{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}],\n"
            "        messages=[msg],\n"
            "    )\n"
        ),
    )
    findings = find_patterns(tmp_path)
    patterns = {f.pattern for f in findings}
    assert "uncached_large_prompt" not in patterns


def test_short_prompt_is_ignored(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent.py",
        'SYSTEM_PROMPT = """You are a helpful assistant."""\n',
    )
    findings = find_patterns(tmp_path)
    assert findings == []


def test_skips_venv_and_node_modules(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / "node_modules").mkdir()
    _write(
        tmp_path,
        ".venv/junk.py",
        "for attempt in range(10):\n    client.chat.completions.create(model='gpt-4o')\n",
    )
    _write(
        tmp_path,
        "src.py",
        "for attempt in range(10):\n    client.chat.completions.create(model='gpt-4o')\n",
    )
    findings = find_patterns(tmp_path)
    assert len(findings) == 1
    assert ".venv" not in str(findings[0].path)


def test_json_serializable(tmp_path: Path) -> None:
    import json

    _write(
        tmp_path,
        "chat.py",
        "for attempt in range(10):\n    client.chat.completions.create(model='gpt-4o')\n",
    )
    findings = find_patterns(tmp_path)
    serialized = json.dumps([f.to_dict() for f in findings])
    assert "retry_loop_no_backoff" in serialized


# ---- Phase 2.1 detectors --------------------------------------------------


def test_dynamic_before_static_cache_break(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent.py",
        'system_prompt = f"User {user_id} asks: handle the following request with context: {DOCS}"\n',
    )
    findings = find_patterns(tmp_path)
    patterns = {f.pattern for f in findings}
    assert "dynamic_before_static_cache_break" in patterns
    assert next(f for f in findings if f.pattern == "dynamic_before_static_cache_break").severity == "high"


def test_dynamic_static_prompt_ok(tmp_path: Path) -> None:
    """No f-string interpolation → no finding."""
    _write(tmp_path, "agent.py", 'system_prompt = "You analyse documents."\n')
    findings = find_patterns(tmp_path)
    assert all(f.pattern != "dynamic_before_static_cache_break" for f in findings)


def test_user_prompt_fstring_is_ok(tmp_path: Path) -> None:
    """user_prompt with f-string is the *correct* pattern — not a cache break."""
    _write(
        tmp_path,
        "agent.py",
        'user_prompt = f"Please summarize: {document_text}"\n',
    )
    findings = find_patterns(tmp_path)
    assert all(f.pattern != "dynamic_before_static_cache_break" for f in findings)


def test_uppercase_system_prompt_caught(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent.py",
        'SYSTEM_PROMPT = f"You are helping {user_id} with their task."\n',
    )
    findings = find_patterns(tmp_path)
    assert any(f.pattern == "dynamic_before_static_cache_break" for f in findings)


def test_unbounded_conversation_history(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "chat.py",
        (
            "def chat(user_input):\n"
            "    messages.append({'role': 'user', 'content': user_input})\n"
            "    response = client.chat.completions.create(model='gpt-4o-mini', messages=messages)\n"
            "    messages.append({'role': 'assistant', 'content': response.choices[0].message.content})\n"
            "    return response\n"
        ),
    )
    findings = find_patterns(tmp_path)
    assert any(f.pattern == "unbounded_conversation_history" for f in findings)


def test_truncated_history_is_ok(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "chat.py",
        (
            "def chat(user_input):\n"
            "    messages.append({'role': 'user', 'content': user_input})\n"
            "    messages = messages[-10:]  # sliding window\n"
            "    return client.chat.completions.create(model='gpt-4o-mini', messages=messages)\n"
        ),
    )
    findings = find_patterns(tmp_path)
    assert all(f.pattern != "unbounded_conversation_history" for f in findings)


def test_agent_loop_no_max_iter(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent.py",
        (
            "def run_agent():\n"
            "    while True:\n"
            "        response = client.chat.completions.create(model='gpt-4o', messages=msgs)\n"
            "        msgs.append(response.choices[0].message)\n"
        ),
    )
    findings = find_patterns(tmp_path)
    f = next(f for f in findings if f.pattern == "agent_loop_no_max_iter")
    assert f.severity == "high"


def test_agent_loop_with_max_iter_is_ok(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "agent.py",
        (
            "def run_agent():\n"
            "    iters = 0\n"
            "    while True:\n"
            "        if iters >= max_iter:\n"
            "            break\n"
            "        iters += 1\n"
            "        response = client.chat.completions.create(model='gpt-4o', messages=msgs)\n"
        ),
    )
    findings = find_patterns(tmp_path)
    assert all(f.pattern != "agent_loop_no_max_iter" for f in findings)


def test_temperature_with_cache_nearby_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "service.py",
        (
            "from functools import lru_cache\n"
            "\n"
            "@lru_cache(maxsize=128)\n"
            "def answer(q):\n"
            "    return client.chat.completions.create(\n"
            "        model='gpt-4o-mini',\n"
            "        messages=[{'role': 'user', 'content': q}],\n"
            "        temperature=0.7,\n"
            "    )\n"
        ),
    )
    findings = find_patterns(tmp_path)
    assert any(f.pattern == "temperature_nonzero_with_cache_hint" for f in findings)


def test_temperature_zero_with_cache_ok(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "service.py",
        (
            "from functools import lru_cache\n"
            "\n"
            "@lru_cache(maxsize=128)\n"
            "def answer(q):\n"
            "    return client.chat.completions.create(\n"
            "        model='gpt-4o-mini',\n"
            "        messages=[{'role': 'user', 'content': q}],\n"
            "        temperature=0,\n"
            "    )\n"
        ),
    )
    findings = find_patterns(tmp_path)
    assert all(f.pattern != "temperature_nonzero_with_cache_hint" for f in findings)


def test_temperature_no_cache_no_finding(tmp_path: Path) -> None:
    """Temperature > 0 alone is fine — only flag when a cache hint is nearby."""
    _write(
        tmp_path,
        "service.py",
        (
            "def answer(q):\n"
            "    return client.chat.completions.create(\n"
            "        model='gpt-4o-mini',\n"
            "        messages=[{'role': 'user', 'content': q}],\n"
            "        temperature=0.7,\n"
            "    )\n"
        ),
    )
    findings = find_patterns(tmp_path)
    assert all(f.pattern != "temperature_nonzero_with_cache_hint" for f in findings)


def test_reasoning_effort_high(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "reason.py",
        "response = client.chat.completions.create(model='o3-mini', reasoning_effort='high', messages=[])\n",
    )
    findings = find_patterns(tmp_path)
    assert any(f.pattern == "reasoning_effort_high_default" for f in findings)


def test_sdk_init_no_timeout(tmp_path: Path) -> None:
    _write(tmp_path, "client.py", "client = OpenAI(api_key='sk-...')\n")
    findings = find_patterns(tmp_path)
    f = next(f for f in findings if f.pattern == "sdk_init_no_timeout")
    assert f.severity == "high"


def test_sdk_init_with_timeout_ok(tmp_path: Path) -> None:
    _write(tmp_path, "client.py", "client = OpenAI(api_key='sk-...', timeout=30.0)\n")
    findings = find_patterns(tmp_path)
    assert all(f.pattern != "sdk_init_no_timeout" for f in findings)


def test_sdk_anthropic_no_timeout(tmp_path: Path) -> None:
    _write(tmp_path, "client.py", "client = Anthropic()\n")
    findings = find_patterns(tmp_path)
    assert any(f.pattern == "sdk_init_no_timeout" for f in findings)


def test_async_sdk_no_timeout(tmp_path: Path) -> None:
    _write(tmp_path, "client.py", "client = AsyncOpenAI(api_key='sk-...')\n")
    findings = find_patterns(tmp_path)
    assert any(f.pattern == "sdk_init_no_timeout" for f in findings)
