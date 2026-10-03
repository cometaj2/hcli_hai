# HCLI
Use this when the goal is about hcli, huckle, or an installed HCLI tool.

- The harness already adds a catalog task for `huckle cli ls`. Do not add another.
- Later tasks must depend on that catalog. Only name a tool the catalog listed.
- One tool invocation per task. Acceptance is that tool's observed output.
- Do not plan an install. If the tool is missing, the task intent is the blocker.
