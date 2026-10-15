import subprocess
import os
import pytest



def run_hai_script(script, check=True):
    command = f"""
#!/bin/bash
set -e

export HCLI_CORE_HOME=~/.hcli_core_test
export HUCKLE_HOME=~/.huckle_test
export HAI_HOME=~/.hai_test

eval $(huckle env)

{script}
"""

    result = subprocess.run(
        ["bash", "-c", command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if check:
        assert result.returncode == 0, (
            f"Command failed with exit code {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    return result


def test_hai_context(gunicorn_server, cleanup):
    hello = """
    hai new > /dev/null 2>&1
    hai context --json
    """

    result = run_hai_script(hello)

    assert (
        '{\n'
        '    "messages": [\n'
        '        {\n'
        '            "content": "",\n'
        '            "role": "system"\n'
        '        }\n'
        '    ],\n'
        '    "name": "",\n'
        '    "title": ""\n'
        '}\n'
        == result.stdout
    )


def test_hai_name(gunicorn_server, cleanup):
    hello = """
    hai new > /dev/null 2>&1

    hai name set hello
    hai name
    """

    result = run_hai_script(hello)

    assert "hello\n" == result.stdout


def test_hai_new_changes_current_context(gunicorn_server, cleanup):
    hello = """
    hai new > /dev/null
    first=$(hai current)

    hai new > /dev/null
    second=$(hai current)

    test -n "$first"
    test -n "$second"
    test "$first" != "$second"
    """

    run_hai_script(hello)


def test_hai_current_returns_context_id(gunicorn_server, cleanup):
    hello = """
    hai new > /dev/null
    current=$(hai current)

    test -n "$current"
    test "${#current}" -gt 0
    """

    run_hai_script(hello)



def test_hai_new_json_is_valid_json(gunicorn_server, cleanup):
    hello = """
    hai new --json > new.json

    test -s new.json

    python - <<'PY'
import json

with open("new.json", encoding="utf-8") as stream:
    new_context = json.load(stream)

assert new_context is not None
PY
    """

    run_hai_script(hello)


def test_hai_context_json_is_valid_json(gunicorn_server, cleanup):
    hello = """
    hai new > /dev/null
    hai context --json > context.json

    python - <<'PY'
import json

with open("context.json", encoding="utf-8") as stream:
    context = json.load(stream)

assert "messages" in context
assert "name" in context
assert "title" in context
PY
    """

    run_hai_script(hello)


def test_hai_name_supports_spaces(gunicorn_server, cleanup):
    hello = """
    hai new > /dev/null

    hai name set "my test conversation"
    result=$(hai name)

    test "$result" = "my test conversation"
    """

    run_hai_script(hello)


def test_hai_name_and_title_are_independent(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai new > /dev/null

    hai name set engineering
    hai title set "Deployment planning"

    test "$(hai name)" = "engineering"
    test "$(hai title)" = "Deployment planning"
    """

    run_hai_script(hello)


def test_hai_title_round_trip(gunicorn_server, cleanup):
    hello = """
    hai new > /dev/null

    hai title set "Release planning"
    result=$(hai title)

    test "$result" = "Release planning"
    """

    run_hai_script(hello)


def test_hai_reset_clears_context_metadata(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai new > /dev/null

    hai name set temporary-name
    hai title set temporary-title

    hai reset > /dev/null
    hai context --json > context.json

    grep -q '"role": "system"' context.json
    grep -q '"content": ""' context.json
    grep -q '"name": ""' context.json
    grep -q '"title": ""' context.json
    """

    run_hai_script(hello)


def test_hai_reset_preserves_current_context_id(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai new > /dev/null
    current_before=$(hai current)

    hai reset > /dev/null
    current_after=$(hai current)

    test -n "$current_before"
    test -n "$current_after"
    test "$current_before" = "$current_after"
    """

    run_hai_script(hello)


def test_hai_ls_contains_created_context(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai new > /dev/null
    context_id=$(hai current)

    hai ls > contexts.txt

    test -s contexts.txt
    grep -q "$context_id" contexts.txt
    """

    run_hai_script(hello)


def test_hai_ls_json_is_valid_json(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai new > /dev/null
    hai ls --json > contexts.json

    test -s contexts.json

    python - <<'PY'
import json

with open("contexts.json", encoding="utf-8") as stream:
    contexts = json.load(stream)

assert contexts is not None
PY
    """

    run_hai_script(hello)


def test_hai_ls_json_contains_created_context(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai new > /dev/null
    context_id=$(hai current)

    hai ls --json > contexts.json

    test -s contexts.json
    grep -q "$context_id" contexts.json
    """

    run_hai_script(hello)


def test_hai_set_switches_context(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai new > /dev/null
    first=$(hai current)

    hai new > /dev/null
    second=$(hai current)

    test "$first" != "$second"
    test "$(hai current)" = "$second"

    hai set "$first" > /dev/null

    test "$(hai current)" = "$first"
    """

    run_hai_script(hello)


def test_hai_rm_removes_context_from_listing(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai new > /dev/null
    removable=$(hai current)

    hai new > /dev/null
    retained=$(hai current)

    test "$removable" != "$retained"

    hai rm "$removable" > /dev/null
    hai ls > contexts.txt

    ! grep -q "$removable" contexts.txt
    grep -q "$retained" contexts.txt
    """

    run_hai_script(hello)


def test_hai_rm_removes_context_from_json_listing(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai new > /dev/null
    removable=$(hai current)

    hai new > /dev/null
    retained=$(hai current)

    hai rm "$removable" > /dev/null
    hai ls --json > contexts.json

    ! grep -q "$removable" contexts.json
    grep -q "$retained" contexts.json
    """

    run_hai_script(hello)


def test_hai_provider_returns_configured_provider(
    gunicorn_server,
    cleanup,
):
    hello = """
    provider=$(hai provider)

    test -n "$provider"
    """

    run_hai_script(hello)


def test_hai_provider_ls_contains_ollama(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai provider ls > providers.txt

    test -s providers.txt
    grep -q "ollama" providers.txt
    """

    run_hai_script(hello)


def test_hai_provider_ls_json_is_valid_json(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai provider ls --json > providers.json

    test -s providers.json

    python - <<'PY'
import json

with open("providers.json", encoding="utf-8") as stream:
    providers = json.load(stream)

assert providers is not None
PY
    """

    run_hai_script(hello)


def test_hai_provider_set_ollama(
    gunicorn_server,
    cleanup,
):
    hello = """
    original=$(hai provider)

    hai provider set ollama > /dev/null

    test "$(hai provider)" = "ollama"

    hai provider set "$original" > /dev/null
    """

    run_hai_script(hello)


def test_hai_provider_set_rejects_invalid_provider(
    gunicorn_server,
    cleanup,
):
    hello = """
    if hai provider set DOES_NOT_EXIST > output.txt 2> error.txt; then
        exit 1
    fi
    """

    run_hai_script(hello)


def test_hai_set_rejects_unknown_context(
    gunicorn_server,
    cleanup,
):
    hello = """
    if hai set DOES_NOT_EXIST > output.txt 2> error.txt; then
        exit 1
    fi
    """

    run_hai_script(hello)


def test_hai_rm_rejects_unknown_context(
    gunicorn_server,
    cleanup,
):
    hello = """
    if hai rm DOES_NOT_EXIST > output.txt 2> error.txt; then
        exit 1
    fi
    """

    run_hai_script(hello)


def test_hai_agent_skill_ls_is_available(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai agent skill ls > skills.txt

    test -f skills.txt
    """

    run_hai_script(hello)


def test_hai_agent_skill_ls_json_is_valid_json(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai agent skill ls --json > skills.json

    test -s skills.json

    python - <<'PY'
import json

with open("skills.json", encoding="utf-8") as stream:
    skills = json.load(stream)

assert skills is not None
PY
    """

    run_hai_script(hello)


def test_hai_help_exposes_primary_commands(
    gunicorn_server,
    cleanup,
):
    hello = """
    hai help > help.txt

    test -s help.txt
    grep -q "context" help.txt
    grep -q "name" help.txt
    grep -q "title" help.txt
    grep -q "provider" help.txt
    grep -q "agent" help.txt
    """

    run_hai_script(hello)
