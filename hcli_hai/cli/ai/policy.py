import bashlex
import re

WHITELIST = frozenset({
    "git", "pwd", "ls", "echo", "grep", "curl", "cat", "head", "tail", "wc", "man", "hat", "huckle", "ddgr",
})

STEP_KEYS = ("status", "goal", "why", "bash", "say")
STEP_STATUSES = ("continue", "done", "need_help")


def bash_error(command_string):
    """Return an error string if the command is not a single simple whitelisted command, else None."""
    command_string = (command_string or "").strip()
    if not command_string:
        return "status=continue requires a non-empty bash"
    if "\n" in command_string:
        return "bash must be one simple command line"

    try:
        trees = bashlex.parse(command_string)
    except Exception as e:
        return "invalid bash syntax: %s" % e

    if len(trees) != 1:
        return "bash must be exactly one command line"

    programs = []
    forbidden = []

    def walk(node):
        kind = getattr(node, "kind", None)
        if kind == "pipeline":
            forbidden.append("pipes")
        elif kind in ("commandsubstitution", "processsubstitution"):
            forbidden.append("substitution")
        elif kind == "redirect":
            forbidden.append("redirects")
        elif kind == "operator":
            forbidden.append("chaining")
        elif kind == "command":
            parts = getattr(node, "parts", None) or []
            if parts and getattr(parts[0], "kind", None) == "word":
                programs.append(parts[0].word)
        for part in getattr(node, "parts", None) or []:
            walk(part)

    walk(trees[0])

    if forbidden:
        return "bash must be one simple command line; no pipes, chaining, redirects, or substitution"
    if len(programs) != 1:
        return "bash must invoke exactly one program, found %s" % programs
    if programs[0] not in WHITELIST:
        return (
            "unauthorized command %r; allowed: %s. "
            "use status=need_help if the task requires it"
            % (programs[0], ", ".join(sorted(WHITELIST)))
        )
    return None


def empty_step():
    return {"status": "", "goal": "", "why": "", "bash": "", "say": ""}


def evidence_met(task):
    """Bare-bones stub. The model decides when the goal is done; no derived evidence checks."""
    return False
