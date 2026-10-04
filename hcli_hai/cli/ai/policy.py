import bashlex
import re

WHITELIST = frozenset({
    "git", "pwd", "ls", "echo", "grep", "curl", "cat", "head", "tail", "wc", "man", "hat", "huckle", "ddgr",
})

STEP_KEYS = ("status", "goal", "why", "bash", "say")
STEP_STATUSES = ("continue", "done", "need_help")
READ_PROGS = ("cat", "head", "tail", "grep")
FILE_NAME = re.compile(r"""[\w./-]+\.(?:py|md|json|rst|txt|toml|cfg|yml|yaml|ini|sh|csv)""")


def bash_error(command_string):
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


def next_runnable(tasks):
    done = {t.get("id") for t in tasks if t.get("status") == "done"}
    for task in tasks:
        if task.get("status") not in ("pending", "active"):
            continue
        deps = task.get("depends_on") or []
        if all(dep in done for dep in deps):
            return task
    return None


def observe_key(bash):
    """Identity of an observation. The harness compares these; the model does not."""
    parts = (bash or "").split()
    if not parts:
        return None
    prog = parts[0]
    args = [p for p in parts[1:] if not p.startswith("-") and not p.isdigit()]
    if prog == "ls":
        return ("ls",)
    if prog == "pwd":
        return ("pwd",)
    if prog in READ_PROGS and args:
        name = args[-1].rstrip("/").split("/")[-1]
        return ("read", name)
    return (prog, tuple(parts[1:]))


def alloc_id(used):
    n = 1
    while ("t%d" % n) in used:
        n += 1
    used.add("t%d" % n)
    return "t%d" % n


def derive_check(intent, acceptance=""):
    """A check the harness can evaluate. kind=observe closes on this task's own key.
    kind=open has no implied command; a done claim still needs this task's own observation.
    """
    text = "%s %s" % (intent or "", acceptance or "")
    low = text.lower()
    named = FILE_NAME.search(text)
    if named and any(word in low for word in ("read", "cat", "open", "show", "contents", "file")):
        path = named.group(0).strip("\"'")
        name = path.rstrip("/").split("/")[-1]
        return {"kind": "observe", "key": ["read", name], "bash": "cat %s" % path}
    if re.search(r"\b(list|ls|listing)\b", low) and re.search(r"\b(director|files?)\b", low):
        return {"kind": "observe", "key": ["ls"], "bash": "ls"}
    if "working directory" in low or re.search(r"\bpwd\b", low):
        return {"kind": "observe", "key": ["pwd"], "bash": "pwd"}
    return {"kind": "open"}


def check_key(task):
    check = task.get("check") or {}
    key = check.get("key") or []
    if not key:
        return None
    return tuple(key)


def task_has_key(task, key):
    if not key:
        return False
    for item in task.get("observations") or []:
        if observe_key(item.get("bash")) == tuple(key):
            return True
    return False


def evidence_met(task):
    """True only for a deterministic check satisfied by this task's observations."""
    check = task.get("check") or {}
    if check.get("kind") != "observe":
        return False
    return task_has_key(task, check_key(task))


def listing_files(text):
    names = []
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("total "):
            continue
        if not raw.startswith("d") and not raw.startswith("l"):
            parts = raw.split()
            if len(parts) >= 2:
                names.append(parts[-1])
    return names

#     """Regular files named by an ls observation. Directories are not files."""
#     names = []
#     for line in (text or "").splitlines():
#         raw = line.strip()
#         if not raw or raw.startswith("total "):
#             continue
#         if raw[:1] in ("d", "l"):
#             continue
#         if raw[:1] == "-":
#             parts = raw.split()
#             if len(parts) >= 8:
#                 names.append(parts[-1])
#             continue
#         for token in raw.split():
#             if token in (".", ".."):
#                 continue
#             names.append(token)
#     seen = []
#     for name in names:
#         if name and name not in seen and name not in (".", ".."):
#             seen.append(name)
#     return seen
