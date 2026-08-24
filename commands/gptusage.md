---
description: Show how much ChatGPT plan quota is left (costs nothing)
---

```bash
"${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/ask-gpt/bin/askgpt usage
```

Report the figure as-is. It reads Codex's own local session log and makes no network
call, so it never consumes the allowance it reports — but it is only as fresh as the
last Codex run, which the output states. If the user wants a current figure, running
any review refreshes it.
