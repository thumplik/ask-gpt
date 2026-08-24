#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

mkdir -p "$CLAUDE_DIR/commands" "$CLAUDE_DIR/skills"

# Replace symlinks freely; never clobber a real file or directory. `ln -sfn` over
# an existing directory does not reliably replace it -- on macOS it can create the
# link *inside* it, leaving Claude reading stale content while we report success.
link() {
  local src="$1" dest="$2"
  if [ -L "$dest" ]; then
    rm -f "$dest"
  elif [ -e "$dest" ]; then
    echo "refusing to replace existing non-symlink: $dest" >&2
    echo "move or delete it, then re-run." >&2
    exit 1
  fi
  ln -s "$src" "$dest"
  [ -L "$dest" ] || { echo "failed to create symlink: $dest" >&2; exit 1; }
  echo "  linked $dest -> $(readlink "$dest")"
}

link "$REPO"                          "$CLAUDE_DIR/ask-gpt"
link "$REPO/commands/gptreview.md"    "$CLAUDE_DIR/commands/gptreview.md"
link "$REPO/commands/askgpt.md"       "$CLAUDE_DIR/commands/askgpt.md"
link "$REPO/commands/gptfollow.md"    "$CLAUDE_DIR/commands/gptfollow.md"
link "$REPO"                          "$CLAUDE_DIR/skills/ask-gpt"

chmod +x "$REPO/bin/askgpt"

echo "Verifying Codex..."
# The path travels via the environment. Interpolating it into a Python string
# literal breaks on any repo path containing a quote or backslash.
ASKGPT_REPO="$REPO" python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ["ASKGPT_REPO"])
from askgpt.codex import find_codex, check_auth
binary = find_codex()
print("  codex: " + binary)
check_auth(binary)
print("  auth:  ok")
PY
echo "Run /gptreview or /askgpt in Claude Code."
