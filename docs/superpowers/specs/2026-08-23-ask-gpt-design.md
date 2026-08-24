# ask-gpt — Design Spec

**Date:** 2026-08-23
**Status:** Approved; pre-implementation
**Repo:** `ask-gpt`

## 1. Problem

Getting a second opinion from GPT on work done in Claude Code currently costs four
manual copy-paste steps per round:

1. Copy Claude's output out of the terminal.
2. Point ChatGPT's GitHub connector at the branch, or paste the diff.
3. Paste both into ChatGPT and ask for an adversarial review.
4. Copy the response back into Claude Code.

The goal is to collapse this to one command, billed against an existing ChatGPT Pro
subscription — no API key, no second bill.

## 2. Verified environment

Every fact below was confirmed empirically on 2026-08-23, not assumed. This section
exists so a future reader can tell which claims were tested.

| Fact | Value | How confirmed |
|---|---|---|
| Codex CLI present | `/Applications/ChatGPT.app/Contents/Resources/codex` | `--version` → `codex-cli 0.148.0-alpha.9` |
| On `PATH`? | **No** | `command -v codex` fails; full path works |
| Auth | ChatGPT account | `codex login status` → "Logged in using ChatGPT" |
| Runs under Claude Code's Bash sandbox | Yes | `--version` succeeds sandboxed |
| Working model slug | **`gpt-5.6-sol`** | live call returned a correct answer |
| Also valid | `gpt-5.6-terra` (lower tier) | live call |
| Rejected slugs | `gpt-5.3-codex`, `gpt-5.6`, `gpt-5.6-codex`, `gpt-5.5-codex`, `gpt-5.4-codex`, `gpt-5.3` | HTTP 400, "not supported when using Codex with a ChatGPT account" |
| Invalid slugs cost quota? | **No** — rejected before inference | 400 returns immediately |
| `codex review` sandbox flag | None exposed | `--help`; review is structurally read-only |
| MCP tools | `codex`, `codex-reply` | stdio JSON-RPC `tools/list` probe |
| `codex` tool params | `prompt`, `model`, `sandbox`, `approval-policy`, `base-instructions`, `developer-instructions`, `cwd`, `config`, `compact-prompt` | same probe |
| `codex-reply` params | `threadId`, `conversationId`, `prompt` | same probe |
| Session-id env var | **Does not exist** | `CLAUDE_CODE_HOST_SESSION_ID` ≠ transcript filename |
| Transcript reduction | 238 KB → 17 KB (~4.3k tokens, 30 turns) | prototype extraction |
| Largest local transcripts | 11–22 MB | `find` over `~/.claude/projects` |

**Side finding, unrelated to this project:** the local `~/.codex/config.toml` sets
`model = "gpt-5.3-codex"`, which this account cannot use. Worth fixing separately.

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | Both a general ask and a dedicated review | Shares one context-packaging layer and one persona file |
| Review context | Task + diff, **no Claude narration** | Claude summarising its own work anchors the reviewer; independence is the point |
| Ask context | **Verbatim** transcript, filtered and capped | No self-serving summary; measured cheap enough to be practical |
| Form factor | Skill + slash commands + MCP server | User chose the full set |
| Response handling | Relay verbatim, then rebut point by point | User sees both sides; nothing changes without their word |
| Model | Pin `gpt-5.6-sol`, `--model` overrides | Config default is invalid, so inheriting it would break |
| Visibility | Public from the first commit | Build in the open |

## 4. Architecture

```
question / review request
        |
        v
  context packager  -->  payload.md   (written to disk, inspectable)
        |                  - task statement (user's words, from transcript)
        |                  - transcript, verbatim, filtered + capped   [ask]
        |                  - adversarial reviewer persona              [review]
        v
  codex CLI  (read-only sandbox, ChatGPT subscription auth)
        |
        v
  out.md  -->  relayed verbatim, then rebutted point by point
```

Codex reads the local working tree, so it sees uncommitted work — strictly more than
the GitHub connector, which only sees what has been pushed.

## 5. Layout

Source of truth is the repo; `install.sh` symlinks into the Claude config dir so the
tool is available in every project and edits apply in one place.

```
ask-gpt/
|-- SKILL.md                        # when to reach for this; how to read results
|-- install.sh                      # symlinks + MCP registration
|-- commands/askgpt.md
|-- commands/gptreview.md
|-- prompts/adversarial-review.md
|-- bin/codex-path.sh               # binary resolution + auth check
|-- bin/pack-transcript.py          # JSONL -> capped markdown
|-- bin/ask-gpt.sh                  # orchestrator
|-- tests/fixtures/                 # synthetic transcripts only
|-- docs/superpowers/specs/
|-- README.md
`-- LICENSE
```

## 6. Components

### 6.1 `bin/codex-path.sh`

Codex is not on `PATH` and the working binary is buried in an app bundle whose path
changes between versions. Resolution order:

1. `$CODEX_BIN`
2. `command -v codex`
3. `/Applications/ChatGPT.app/Contents/Resources/codex`
4. `$HOME/.codex/bin/codex`

Then verify `codex login status`. If not logged in, print instructions for the user to
run `codex login` themselves. The tool never drives an auth flow.

### 6.2 `bin/pack-transcript.py`

The piece that replaces copy-paste. Pure function, therefore the only unit-tested
component.

- Keep records of type `user` and `assistant`; drop everything else.
- Keep `text` blocks. Collapse `tool_use` to a one-line `[tool: Bash]` marker.
- Drop `tool_result` payloads and `<system-reminder>` blocks entirely — this is where
  the 93% size reduction comes from.
- Accumulate newest-first against a character budget (default 60,000). On truncation,
  prepend `[earlier turns omitted]`.
- Drop the trailing turn that invoked the command, so GPT does not read "user asked to
  ask GPT".
- Secret scan (`sk-`, `ghp_`, `AKIA`, bearer tokens). On a hit, **halt and ask** — never
  scrub silently, because silent scrubbing teaches false confidence.

**Session resolution.** No environment variable carries the Claude session id, and
`CLAUDE_CODE_HOST_SESSION_ID` is a different identifier. Therefore: accept an explicit
`--session-id` (Claude knows it and passes it), and fall back to the most recently
modified `.jsonl` in the project directory. The fallback alone would pick the wrong
file when two sessions are open in one directory; the explicit argument removes that.

Honors `$CLAUDE_CONFIG_DIR`, defaulting to `~/.claude`. No hardcoded home paths.

### 6.3 `prompts/adversarial-review.md`

The reviewer persona. Substance:

- No incentive to be agreeable. Assume the code is wrong until shown otherwise.
- Defects only. No praise, no restating what the code does.
- Per finding: `file:line`, severity (Blocker / High / Medium / Low), the concrete
  failure scenario, and a repro or test that would expose it.
- Flag code that exists to satisfy a test rather than solve the problem.
- Flag anything the task asked for that is missing — scope gaps, not only bugs.
- **If there is no real defect, say so. Do not manufacture findings.** Adversarial
  framing reliably induces invented problems, and a reviewer that cries wolf stops
  being read.
- Close with "would I merge this: yes/no" and the single largest residual risk.

## 7. Command surface

### `/gptreview [--base <branch> | --uncommitted | --commit <sha>]`

```
codex review --base <branch> - < payload.md
```

Target auto-detection: on a non-default branch, `--base <default-branch>`; otherwise
`--uncommitted`. Payload is the persona plus a `<TASK>` block holding the **user's own
words, never a Claude summary**.

Resolving the task statement, in order:

1. An explicit `--task "<text>"` or `--task-file <path>`.
2. A spec file in `docs/superpowers/specs/` matching the current branch, if one exists.
3. The first user message of the session.

Rule 3 is only correct for a session covering a single task; in a long session spanning
several, it resolves to the wrong one. When the command falls back to rule 3 it prints
the task statement it selected, so a wrong pick is visible before the review runs rather
than after. `codex review` exposes no sandbox flag, so it cannot edit files.

### `/askgpt <question>`

```
codex exec -s read-only --skip-git-repo-check -m gpt-5.6-sol -o out.md - < payload.md
```

Payload is the question, the packed verbatim transcript, and a repo pointer.
`-s read-only` is explicit because `exec`, unlike `review`, can write.

### `/askgpt --follow <text>`

```
codex exec resume --last -s read-only - < followup.md
```

The argue-back loop.

## 8. MCP path

Registered by `install.sh` using the resolved binary path:

```
claude mcp add codex -- <resolved-codex> mcp-server
```

Exposes `codex` and `codex-reply` (verified in §2).

**When to use which path.** The `prompt` parameter is a plain string, so MCP *can*
carry a verbatim transcript — but the payload must pass through Claude's context to get
there, whereas the CLI pipes it from disk directly into Codex. On a 60k-character
payload that is roughly 15k tokens of context window per call.

Therefore:

- **Heavy first-shot payloads → CLI.** Transcript and diff never enter Claude's context.
- **Follow-up debate on an established thread → MCP.** Context already lives server-side
  behind `threadId`; the marginal prompt is short, and native tool calls avoid a Bash
  round-trip.

Defaults are wired accordingly. This is a documented trade-off, not a limitation.

## 9. Response handling

GPT's response is printed verbatim in a quoted block. Claude then goes finding by
finding: **Agree** / **Disagree, because…** / **Need to check first**. Then it stops.
Nothing is modified without the user's word. The `superpowers:receiving-code-review`
skill is invoked to guard against performative agreement.

**GPT output is untrusted data, not instructions.** Codex reads the repository, so
repository contents can reach GPT's output. If a response contains directives ("run
this", "delete that"), they are surfaced to the user, never executed.

## 10. Privacy

The ask path uploads conversation content to OpenAI, and conversations contain more
than users picture — file contents, paths, pasted material. Mitigations:

- Payload is always written to disk before sending, so it can be inspected.
- `--dry-run` on both commands builds the payload and sends nothing.
- The secret scan halts on a hit rather than scrubbing.
- The README states this above the fold.

Absent a scan hit, sending proceeds without an extra prompt: the goal was to remove
friction, not relocate it.

## 11. Error handling

| Condition | Behaviour |
|---|---|
| Codex binary not found | List every path tried |
| Not logged in | Instruct the user to run `codex login`; never drive auth |
| Not a git repo | `--skip-git-repo-check` for ask; `/gptreview` errors clearly |
| Empty diff | Do not call GPT at all |
| Non-zero exit | Surface stderr verbatim; **never silently retry** — retries burn quota |
| Quota exhausted | Report plainly |

## 12. Testing

- `pack-transcript.py` — golden-file tests over **synthetic, hand-authored** fixtures.
  Never a redacted real transcript: redaction fails, and real transcripts are private
  conversations. Cases: budget enforcement, `tool_result` stripping, system-reminder
  stripping, secret detection, session resolution and its fallback.
- `codex-path.sh` — resolution-order tests using a stub binary on `PATH`.
- Both commands — `--dry-run` against real transcripts, no network.
- One live smoke test per command, run manually.

## 13. Out of scope

- Any use of the OpenAI API or an API key.
- Automatic application of GPT's suggestions.
- Reviewers other than Codex/GPT.
- CI integration.
