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

# Preflight: refuse if ANY destination is an occupied non-symlink, before we
# change a single link. A partial install that stopped at the fourth target
# used to leave the first three already swapped.
preflight_dest() {
  local dest="$1"
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    echo "refusing to replace existing non-symlink: $dest" >&2
    echo "move or delete it, then re-run." >&2
    exit 1
  fi
}
for d in "$CLAUDE_DIR/ask-gpt" "$CLAUDE_DIR/commands/gptreview.md" \
         "$CLAUDE_DIR/commands/askgpt.md" "$CLAUDE_DIR/commands/gptfollow.md" \
         "$CLAUDE_DIR/commands/gptusage.md" "$CLAUDE_DIR/skills/second-opinion" \
         "${ASKGPT_BIN_DIR:-$HOME/.local/bin}/askgpt"; do
  preflight_dest "$d"
done

link "$REPO"                          "$CLAUDE_DIR/ask-gpt"
link "$REPO/commands/gptreview.md"    "$CLAUDE_DIR/commands/gptreview.md"
link "$REPO/commands/askgpt.md"       "$CLAUDE_DIR/commands/askgpt.md"
link "$REPO/commands/gptfollow.md"    "$CLAUDE_DIR/commands/gptfollow.md"
link "$REPO/commands/gptusage.md"     "$CLAUDE_DIR/commands/gptusage.md"
# Retire the old skill name: it sat beside /askgpt looking like a duplicate.
# Only ever removes a symlink, and only one pointing back into this repo.
OLD_SKILL="$CLAUDE_DIR/skills/ask-gpt"
if [ -L "$OLD_SKILL" ] && [ "$(readlink "$OLD_SKILL")" = "$REPO" ]; then
  rm -f "$OLD_SKILL"
  echo "  removed superseded skill link $OLD_SKILL"
fi

link "$REPO"                          "$CLAUDE_DIR/skills/second-opinion"

chmod +x "$REPO/bin/askgpt"

# Put the CLI on PATH so `askgpt usage` works from any terminal, not just via
# its full path.
BIN_DIR="${ASKGPT_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN_DIR"
link "$REPO/bin/askgpt" "$BIN_DIR/askgpt"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  note: $BIN_DIR is not on your PATH; add it to use \`askgpt\` directly" ;;
esac

echo "Verifying Codex..."
# The path travels via the environment. Interpolating it into a Python string
# literal breaks on any repo path containing a quote or backslash.
ASKGPT_REPO="$REPO" python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ["ASKGPT_REPO"])
from askgpt.codex import find_codex, check_auth, version, version_warning
binary = find_codex()
print("  codex: " + binary)
print("  build: " + (version(binary) or "unknown"))
check_auth(binary)
print("  auth:  ok")
caveat = version_warning(binary)
if caveat:
    print()
    print("  WARNING: " + caveat.replace("\n", "\n           "))
PY
echo
echo "----------------------------------------------------------------------"
echo "Before you use this: Codex's read-only sandbox prevents WRITES, not"
echo "reads. Reads are NOT confined to the repository -- a read-only run was"
echo "measured reading a file in \$HOME. Anything your user account can read"
echo "is reachable, including ~/.ssh and ~/.aws. The preflight scans the"
echo "repository because that is where accidents usually are, not because"
echo "reads stop there. Use a container if that reach is unacceptable."
echo "----------------------------------------------------------------------"
echo
echo "Commands: /gptreview  /askgpt  /gptfollow  /gptusage"
echo "Terminal: askgpt usage"
