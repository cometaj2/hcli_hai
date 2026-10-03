import re
import logger

log = logger.Logger()


# --- hard do -------------------------------------------------------------
_CLI = re.compile(r"(?i)^\s*(hcli|/|!|\$)\b")
_URL = re.compile(
    r"(?i)\b("
    r"https?://\S+"
    r"|www\.\S+"
    r"|[a-z0-9-]+\.(com|org|net|io|ai|dev|app|co|info|edu|gov)\b"
    r")"
)
_FLAGS = re.compile(r"(^|\s)(-[a-zA-Z]|--[a-zA-Z0-9-]+)\b")
_PATH = re.compile(
    r"(?i)(^|\s)("
    r"~\/\S+"
    r"|\.\/\S+"
    r"|\/(?:home|tmp|var|etc|opt|usr|root)\/\S*"
    r"|[a-z0-9._-]+\.(py|sh|bash|zsh|json|yaml|yml|toml|md|txt|log|cfg|ini|conf)"
    r")\b"
)

# --- code detection (improved) -------------------------------------------
def _looks_like_code_dump(text: str) -> bool:
    """Returns True if the input looks like a pasted code file (e.g. router.py)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 5:
        return False

    code_signals = 0
    for line in lines:
        if re.match(r"^[A-Z_][A-Z0-9_]*\s*=\s*re\.compile", line):
            code_signals += 1
        elif re.match(r"^(async )?def ", line):
            code_signals += 1
        elif re.match(r"^(import |from )", line):
            code_signals += 1
        elif re.match(r"^log = logger\.", line):
            code_signals += 1
        elif re.match(r"^_[A-Z_]+\s*=", line):
            code_signals += 1

    return code_signals >= 4


# --- action frames -------------------------------------------------------
_WEB_VERBS = re.compile(
    r"(?i)\b("
    r"search\s+(for|the\s+web|online|google)|"
    r"searching\s+(for|the\s+web)|"
    r"google|googling|bing|"
    r"look\s*up|looking\s*up|"
    r"browse|browsing|navigate|navigating|visit|visiting|"
    r"open\s+(the\s+)?(web|site|page|link|url)|"
    r"go\s+to|goto|check\s+online|look\s+online|"
    r"find\s+(me\s+)?(on\s+)?(the\s+)?(web|internet|online)|"
    r"pull\s+up|bring\s+up"
    r")\b"
)
_WEB_OBJECTS = re.compile(
    r"(?i)\b(web|website|site|webpage|page|url|link|browser|internet|online|google)\b"
)
_GIT_VERBS = re.compile(
    r"(?i)\b("
    r"git|clone|commit|commits|push|pushed|pull|pulled|fetch|fetched|"
    r"rebase|rebase-i|merge|merged|branch|checkout|switch|"
    r"stash|unstash|add|stage|staged|unstage|"
    r"diff|log|status|blame|tag|tags|"
    r"cherry-pick|revert|reset --hard|reset --soft"
    r")\b"
)
_GIT_OBJECTS = re.compile(
    r"(?i)\b("
    r"git|repo|repository|repositories|branch|branches|"
    r"commit|commits|pr|pull request|remote|remotes|"
    r"origin|upstream|gitignore|submodule"
    r")\b"
)
_SHELL_VERBS = re.compile(
    r"(?i)\b("
    r"run|running|invoke|invoking|execute|executing|"
    r"call|calling|launch|launching|"
    r"list|listing|ls|show|showing|"
    r"get|getting|fetch|fetching|retrieve|"
    r"install|uninstall|upgrade|update|"
    r"start|stop|restart|kill|pkill|"
    r"chmod|chown|sudo|systemctl|journalctl|"
    r"make|build|compile|pip|uv|npm|pacman|"
    r"python|bash|zsh|sh"
    r")\b"
)
_SHELL_OBJECTS = re.compile(
    r"(?i)\b("
    r"command|commands|shell|terminal|bash|zsh|"
    r"process|processes|service|daemon|"
    r"package|packages|script|scripts|"
    r"locally|local machine|this machine|this computer|my computer|"
    r"on (the|this|my) (box|host|machine|computer|system)"
    r")\b"
)
_FILE_VERBS = re.compile(
    r"(?i)\b("
    r"open|opening|read|reading|cat|head|tail|"
    r"inspect|inspecting|examine|examining|review|reviewing|"
    r"explore|exploring|skim|skimming|scan|scanning|"
    r"look\s+(at|through|in|around)|"
    r"write|writing|edit|editing|create|creating|"
    r"delete|deleting|remove|removing|rm|unlink|"
    r"move|moving|mv|copy|copying|cp|"
    r"mkdir|rmdir|touch|stat|find|locate|"
    r"save|saving|append|overwrite"
    r")\b"
)
_FILE_OBJECTS = re.compile(
    r"(?i)\b("
    r"file|files|folder|folders|directory|directories|dir|"
    r"path|paths|filename|config|dotfile|dotfiles|"
    r"disk|filesystem|home directory|"
    r"repo|repository|repositories|codebase|workspace|project|"
    r"folder structure|directory tree|working tree|cwd"
    r")\b"
)
_HCLI_OBJECTS = re.compile(
    r"(?i)\b(hcli|plugin|plugins|endpoint|resource|context|cli|tool|tools)\b"
)
_REQUEST = re.compile(
    r"(?i)\b("
    r"please\s+(run|do|open|get|fetch|search|list|show|install)|"
    r"(can|could|would|will)\s+you\s+(run|do|open|get|fetch|search|list|show|install|look\s*up)|"
    r"i want you to|i need you to|go ahead and|"
    r"do me a favor and"
    r")\b"
)

# --- talk frames ---------------------------------------------------------
_TALK_CUES = re.compile(
    r"(?i)\b("
    r"what is|what's|whats|what are|what does|what do|"
    r"explain|describe|tell me about|tell me how|walk me through|"
    r"how does|how do|how is|how are|how (?:a |an |the )?\w[\w-]* work|"
    r"why is|why does|why do|"
    r"difference between|mean by|meant by|in other words|"
    r"what does that mean|"
    r"(can|could|would)\s+you\s+(tell|explain|describe|walk)"
    r")\b"
)
_HYPOTHETICAL = re.compile(
    r"(?i)\b("
    r"if i|if you|would it|would that|could i just|"
    r"what would happen|suppose i|say i ran|"
    r"hypothetically|in theory"
    r")\b"
)
_GIT_CMD = re.compile(r"(?i)\bgit\s+\w+")
_SEARCH_FOR = re.compile(
    r"(?i)\b(search|look up|lookup|find)\s+(for\s+)?(?!engines?\b)\S+"
)
_ONLINE = re.compile(r"(?i)\b(on the web|on google|online)\b")
_LOCAL = re.compile(
    r"(?i)\b("
    r"on (this|my) (machine|computer|box|system)|locally|"
    r"local (files?|folders?|director(?:y|ies)|repos?|repositor(?:y|ies)|codebase|tree)|"
    r"(this|the|current|working) (folder|directory|dir|repo|repository|codebase|tree|files)|"
    r"current folder structure|here in (the )?(repo|folder|directory)"
    r")\b"
)


def _score(text):
    t = (text or "").strip()
    if not t:
        return 0.0

    s = 0.0
    low = t.lower()

    # --- Code dump detection (fixed for inception) ---
    if _looks_like_code_dump(t):
        # Only treat as "do" if user explicitly asks for action
        # outside of what looks like code.
        explicit_action = any(phrase in low for phrase in [
            "run this", "execute this", "fix this", "debug this",
            "please run", "can you run", "could you run"
        ])
        if not explicit_action:
            return 0.10
    # -------------------------------------------------

    web_v = bool(_WEB_VERBS.search(t))
    web_o = bool(_WEB_OBJECTS.search(t))
    git_v = bool(_GIT_VERBS.search(t))
    git_o = bool(_GIT_OBJECTS.search(t))
    sh_v = bool(_SHELL_VERBS.search(t))
    sh_o = bool(_SHELL_OBJECTS.search(t))
    file_v = bool(_FILE_VERBS.search(t))
    file_o = bool(_FILE_OBJECTS.search(t))
    hcli_o = bool(_HCLI_OBJECTS.search(t))
    asked = bool(_REQUEST.search(t))
    has_url = bool(_URL.search(t))
    has_path = bool(_PATH.search(t))
    has_cli = bool(_CLI.search(t))
    has_git_cmd = bool(_GIT_CMD.search(t))
    talk = bool(_TALK_CUES.search(t))
    hypo = bool(_HYPOTHETICAL.search(t))

    concrete = has_cli or has_url or has_path or has_git_cmd
    actionish = web_v or git_v or sh_v or file_v or concrete

    if has_cli:
        s += 0.90
    if has_url:
        s += 0.70
    if has_path:
        s += 0.55
    if _FLAGS.search(t):
        s += 0.20

    if web_v:
        s += 0.55
    if web_v and web_o:
        s += 0.25

    if git_v:
        s += 0.50
    if git_v and git_o:
        s += 0.25
    if has_git_cmd:
        s += 0.35

    if sh_v:
        s += 0.30
    if sh_v and sh_o:
        s += 0.25
    if sh_o and asked:
        s += 0.20

    if file_v:
        s += 0.30
    if file_v and file_o:
        s += 0.25
    if file_o and asked:
        s += 0.20

    if hcli_o and (sh_v or asked):
        s += 0.20
    if asked and actionish:
        s += 0.20

    if _SEARCH_FOR.search(t) and (web_v or has_url):
        s += 0.30
    if _ONLINE.search(t) and not talk:
        s += 0.25
    if _LOCAL.search(t):
        s += 0.30
    if _LOCAL.search(t) and (file_v or file_o or git_o):
        s += 0.25
    local_files = bool(_LOCAL.search(t) and (file_v or file_o))
    if talk and not concrete and not local_files:
        s -= 0.70
    if hypo and not concrete:
        s -= 0.45
    if t.endswith("?") and talk:
        s -= 0.15
    if low.startswith(
        ("what ", "why ", "how ", "explain ", "can you tell", "could you tell")
    ):
        s -= 0.25
    if talk and (web_o or git_o or file_o) and not concrete:
        s -= 0.15

    return max(0.0, min(1.0, s))


def route_ollama(client, model, text):
    text = (text or "").strip()
    if not text:
        return "talk"
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=1,
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify the user turn. Reply with one word only: do or talk.\n"
                    "do = they want an action now: run a command, git, files, "
                    "shell, HCLI, search or open the web, read/write local paths, "
                    "inspect or read the local repo, current directory, or folder. "
                    "A request to summarize after reading local files is still do.\n"
                    "talk = they want an explanation, opinion, or conversation. "
                    "Questions like 'how does X work' or 'tell me about X' are talk."
                ),
            },
            {"role": "user", "content": text},
        ],
    )
    word = (response.choices[0].message.content or "talk").strip().lower()
    if word.startswith("do"):
        return "do"
    return "talk"


def skill_area(text):
    """Pick one skill file from the same frames used for scoring. Not a do/talk decision."""
    t = (text or "").strip()
    if not t:
        return None
    file_v = bool(_FILE_VERBS.search(t))
    file_o = bool(_FILE_OBJECTS.search(t))
    if _LOCAL.search(t) and (file_v or file_o):
        return "LOCAL"
    if _WEB_VERBS.search(t) or _URL.search(t) or _ONLINE.search(t):
        return "WEB"
    if _GIT_VERBS.search(t) or _GIT_OBJECTS.search(t) or _GIT_CMD.search(t):
        return "GIT"
    if _HCLI_OBJECTS.search(t):
        return "HCLI"
    return None


def froute(text):
    score = _score(text)
    log.info("fast score relative to 'doing': " + str(score))
    if score >= 0.70:
        decision = "do"
        log.info("fast classification: " + decision)
    else:
        decision = "talk"
        log.info("fast classification " + decision)
    return decision


def sroute(client, model, text):
    score = _score(text)
    log.info("fast score relative to 'doing': " + str(score))
    if score >= 0.70:
        decision = "do"
        log.info("fast classification: " + decision)
    elif score <= 0.40:
        decision = "talk"
        log.info("fast classification: " + decision)
    else:
        decision = route_ollama(client, model, text)
        log.info("model classification: " + decision)
    return decision
