# HCLI
Use this when the goal is about hcli, huckle, or an installed HCLI tool.

- First task: list installed hclis with `huckle cli ls`.
- Later tasks must depend on that list. Only name a tool the catalog listed.
- One tool invocation per task. Acceptance is that tool's observed output.
- If you need help with the tool, user the tool name command followed by 'help'.
- Do not plan an install. If the tool is missing, the task intent is the blocker.
