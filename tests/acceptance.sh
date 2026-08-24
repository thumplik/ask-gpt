#!/usr/bin/env bash
# End-to-end acceptance against a fresh install, using a stub Codex.
#
# This layer exists because it is the one that found the bugs that mattered:
# auth rejecting real users, quota detection discarding valid reviews, the
# follow path being unreachable, documented commands that could not be run.
# None of those were caught by unit tests.
#
# Streams are captured SEPARATELY throughout. Merging them with 2>&1 during
# contract discovery is exactly what hid the auth bug: `codex login status`
# writes to stderr, and a merged capture made it look like stdout.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PASS=0; FAIL=0

ok(){ PASS=$((PASS+1)); echo "  PASS  $1"; }
no(){ FAIL=$((FAIL+1)); echo "  FAIL  $1"; [ -n "${2:-}" ] && echo "        $2"; }
check(){ if [ "$1" = true ]; then ok "$2"; else no "$2" "${3:-}"; fi; }

# A stub Codex: answers `login status` on STDERR, exactly as the real CLI does.
cat > "$WORK/codex" <<'PY'
#!/usr/bin/env python3
import sys, pathlib
argv = sys.argv
pathlib.Path(__file__ + ".argv").write_text(" ".join(argv))
if "login" in argv:
    sys.stderr.write("Logged in using ChatGPT\n")   # stderr, like the real one
    sys.exit(0)
if "--fail" in pathlib.Path(__file__ + ".mode").read_text() if pathlib.Path(__file__ + ".mode").exists() else False:
    pass
mode = pathlib.Path(__file__ + ".mode")
if mode.exists() and mode.read_text().strip() == "fail":
    print('{"type":"error","message":"connection reset"}')
    sys.exit(1)
out = argv[argv.index("-o") + 1]
pathlib.Path(out).write_text("STUB REVIEW BODY")
print('{"type":"thread.started","thread_id":"ACCEPT1"}')
PY
chmod +x "$WORK/codex"
export CODEX_BIN="$WORK/codex"
export CLAUDE_CONFIG_DIR="$WORK/claude"
export ASKGPT_BIN_DIR="$WORK/bin"
export ASKGPT_STATE_DIR="$WORK/state"
mkdir -p "$CLAUDE_CONFIG_DIR"

echo "1. install from a fresh checkout"
FRESH="$WORK/fresh"
git clone -q "$REPO" "$FRESH" 2>/dev/null
( cd "$FRESH" && ./install.sh ) >"$WORK/i.out" 2>"$WORK/i.err"; rc=$?
check "$([ $rc -eq 0 ] && echo true || echo false)" "installer exits 0" "$(head -3 "$WORK/i.err")"
check "$(grep -q 'auth:  ok' "$WORK/i.out" && echo true || echo false)" "installer verifies auth"
for c in gptreview askgpt gptfollow gptusage; do
  check "$([ -L "$CLAUDE_CONFIG_DIR/commands/$c.md" ] && echo true || echo false)" "/$c installed"
done
check "$([ -x "$ASKGPT_BIN_DIR/askgpt" ] && echo true || echo false)" "askgpt is on PATH"

echo "2. run the primary command from ANOTHER repository"
OTHER="$WORK/other"; mkdir -p "$OTHER"
git init -q -b main "$OTHER"; git -C "$OTHER" config user.email a@b.c; git -C "$OTHER" config user.name T
echo hello > "$OTHER/f.txt"; git -C "$OTHER" add -A; git -C "$OTHER" commit -qm init
echo changed > "$OTHER/f.txt"
"$ASKGPT_BIN_DIR/askgpt" review --uncommitted --task "acceptance" --session-id S1 \
  --cwd "$OTHER" >"$WORK/r.out" 2>"$WORK/r.err"; rc=$?
check "$([ $rc -eq 0 ] && echo true || echo false)" "review exits 0 from another repo" "$(head -3 "$WORK/r.err")"
check "$(grep -q 'STUB REVIEW BODY' "$WORK/r.out" && echo true || echo false)" "review body on STDOUT"
check "$(grep -q 'Target:' "$WORK/r.err" && echo true || echo false)" "progress on STDERR, not stdout"
check "$(grep -q 'read-only' "$WORK/codex.argv" && echo true || echo false)" "codex invoked read-only"
check "$(grep -q 'gpt-5.6-sol' "$WORK/codex.argv" && echo true || echo false)" "pinned model requested"

echo "3. exercise a failure response"
echo fail > "$WORK/codex.mode"
"$ASKGPT_BIN_DIR/askgpt" review --uncommitted --task "acceptance" --cwd "$OTHER" \
  >"$WORK/f.out" 2>"$WORK/f.err"; rc=$?
check "$([ $rc -ne 0 ] && echo true || echo false)" "failure exits non-zero"
check "$(grep -q 'connection reset' "$WORK/f.err" && echo true || echo false)" "real cause surfaced"
check "$([ ! -s "$WORK/f.out" ] && echo true || echo false)" "no review printed on failure"
rm -f "$WORK/codex.mode"

echo "4. continue the resulting thread"
check "$(grep -q ACCEPT1 "$ASKGPT_STATE_DIR/threads/S1.json" 2>/dev/null && echo true || echo false)" "thread id persisted"
"$ASKGPT_BIN_DIR/askgpt" follow "and another thing" --session-id S1 --cwd "$OTHER" \
  >"$WORK/fo.out" 2>"$WORK/fo.err"; rc=$?
check "$([ $rc -eq 0 ] && echo true || echo false)" "follow exits 0" "$(head -3 "$WORK/fo.err")"
check "$(grep -q 'resume ACCEPT1' "$WORK/codex.argv" && echo true || echo false)" "resumed by exact id"
check "$(grep -qv -- '--last' "$WORK/codex.argv" && echo true || echo false)" "never used --last"

echo "5. follow the README from scratch"
check "$(grep -q 'not confined to the repository\|NOT confined to the repository' "$WORK/i.out" && echo true || echo false)" \
      "installer discloses the read boundary"
check "$(grep -q 'optional' "$REPO/README.md" && echo true || echo false)" \
      "README declares superpowers optional"
check "$(grep -q 'If the .superpowers:receiving-code-review. skill is available' "$REPO/commands/gptreview.md" && echo true || echo false)" \
      "gptreview does not hard-depend on superpowers"
for cmd in /gptreview /askgpt /gptfollow /gptusage; do
  check "$(grep -q -- "$cmd" "$REPO/README.md" && echo true || echo false)" "README documents $cmd"
done
for flag in --dry-run --keep --no-fallback --allow-secrets --allow-sensitive-files; do
  check "$("$ASKGPT_BIN_DIR/askgpt" review --help 2>/dev/null | grep -q -- "$flag" && echo true || echo false)" \
        "README flag $flag exists in the CLI"
done
"$ASKGPT_BIN_DIR/askgpt" usage >"$WORK/u.out" 2>"$WORK/u.err"
check "$([ $? -eq 0 ] && echo true || echo false)" "usage runs"

echo
echo "acceptance: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
