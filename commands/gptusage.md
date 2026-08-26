---
description: Best-effort snapshot of ChatGPT plan usage so far (costs nothing)
---

```bash
"${CLAUDE_PLUGIN_ROOT:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/ask-gpt}"/bin/askgpt-run usage
```

Report the figure as usage so far, not as remaining allowance. It reads Codex's own local session log and makes no network
call, so it never consumes the allowance it reports — but it is only as fresh as the
last Codex run, which the output states. If the user wants a current figure, running
any review refreshes it.
