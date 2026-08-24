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
| `codex review` + target flag + prompt | **Mutually exclusive** | `error: the argument '--uncommitted' cannot be used with '[PROMPT]'`; same for `--base` |
| `codex review` model pinning | **Impossible** — no `-m` flag | `--help`; would silently use the invalid config default |
| Sol tools under `-s read-only` | **Working** | live call ran `git status --porcelain` and read a file |
| `exec resume` sandbox flag | Must precede `resume` | `codex exec resume --last -s read-only` → `unexpected argument '-s'`; `codex exec -s read-only resume <id>` works |
| Thread-id capture | `--json` line 1: `{"type":"thread.started","thread_id":"..."}` | live call |
| Thread continuity by exact id | Verified | resumed session correctly recalled the prior message |
| `--ignore-user-config` | Works; auth still resolves | live call |
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
| Ask context | Dialogue verbatim; tool payloads omitted, capped | No self-serving summary; measured cheap enough to be practical |
| Form factor | Skill + slash commands, **CLI only** | MCP cut from v1: a third execution path that routes payloads through Claude's context and duplicates `resume` |
| Codex primitive | `codex exec` for **both** paths | `codex review` cannot take a custom prompt alongside a target flag, and cannot pin a model |
| Response handling | Relay verbatim, then rebut point by point | User sees both sides; nothing changes without their word |
| Model | Pin `gpt-5.6-sol` + `--ignore-user-config`; **fail closed** | Config default is invalid; never silently downgrade to Terra — asking Sol is the entire point |
| Session continuity | Capture exact `thread_id`; never `--last` | `--last` can attach a follow-up to an unrelated Codex session |
| Visibility | Public from the first commit | Build in the open |

## 4. Architecture

```
question / review request
        |
        v
  context packager  -->  payload.md   (written to disk, inspectable)
        |                  - task statement (user's words, from transcript)
        |                  - dialogue verbatim, tool payloads omitted [ask]
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
|-- install.sh                      # symlinks
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

The piece that replaces copy-paste. Pure function, therefore the most heavily unit-tested
component.

**Terminology.** This produces a *filtered* transcript, not a verbatim one: human and
assistant dialogue is preserved word for word, tool payloads are omitted. Docs must say
"dialogue verbatim, tool payloads omitted" — calling the artefact "the verbatim
transcript" overstates what is sent.

- Keep records of type `user` and `assistant`; drop everything else.
- Keep `text` blocks. Collapse `tool_use` to a one-line `[tool: Bash]` marker.
- Drop `<system-reminder>` blocks entirely.
- Drop **successful** `tool_result` payloads — this is where the 93% size reduction comes
  from. **Retain failed ones** (non-zero exit / error), capped at 3 KB each and 12 KB
  total, oldest dropped first. When Claude has spent an hour chasing a compiler error,
  the failure output is the single most valuable evidence in the session; dropping every
  tool result throws away exactly the part a reviewer needs.
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

Both paths use one primitive, `codex exec`. `codex review` is **not** used: its target
flags (`--base`, `--commit`, `--uncommitted`) are mutually exclusive with a custom
`[PROMPT]` at argument-parse time, so the adversarial persona cannot be combined with
target selection; and it exposes no `-m`, so it cannot pin a model and would fall through
to the invalid config default. Both verified in §2.

Shared invocation:

```
codex exec -m gpt-5.6-sol -s read-only --ignore-user-config --json -o out.md - < payload.md
```

`--ignore-user-config` makes the reviewer reproducible: it skips `~/.codex/config.toml`
(including its invalid model default and any unrelated user-level MCP servers or
policies) while `CODEX_HOME` still resolves authentication. If `gpt-5.6-sol` is
unavailable the command **fails closed** — it never downgrades to `gpt-5.6-terra`,
because obtaining Sol's judgement is the entire purpose.

### `/gptreview [--base <branch> | --uncommitted | --commit <sha>]`

Target selection happens locally, in the prompt, not via Codex flags. The command
resolves the target, generates the git context, and instructs Sol which range to inspect.
Codex reads the working tree directly under `read-only`, so it inspects real files rather
than a pre-rendered diff.

**Target semantics, defined explicitly:**

| Invocation | Includes |
|---|---|
| `--uncommitted` | staged + unstaged + untracked |
| `--commit <sha>` | exactly that commit |
| `--base <branch>` | commits since `git merge-base <branch> HEAD`, **plus** current staged, unstaged, and untracked work |
| auto-detected (non-default branch) | identical to `--base <default-branch>` |
| auto-detected (on default branch) | identical to `--uncommitted` |

`--base` deliberately includes the dirty working tree — it answers "what would ship if I
merged right now," not "what have I committed." This matters more here than in a normal
review tool: the expected usage is asking for a review immediately after Claude writes
code and before anything is committed. A target that silently reviewed yesterday's
commits while ignoring code written seconds ago would look like it worked, which is worse
than failing.

The default branch is resolved from `origin/HEAD`, falling back to `main`, then `master`.
The resolved target and its file count are printed before dispatch.

Payload is the adversarial persona plus a `<TASK>` block containing the **user's own
words, never a Claude summary**.

**Task resolution, in order:**

1. Explicit `--task "<text>"` or `--task-file <path>`.
2. A spec file under `docs/superpowers/specs/` matching the current branch.
3. **No authoritative task.** The prompt then states plainly that the original
   requirement is unknown and asks for review on correctness, regressions, security, and
   maintainability only.

There is deliberately no "first user message of the session" fallback. In precisely the
long, multi-task sessions this tool is built for, that heuristic confidently supplies the
*wrong* assignment — and a reviewer working from a wrong requirement produces confident,
misdirected findings. Missing context is recoverable; wrong context is not.

### `/askgpt <question>`

Same runner, with the filtered conversation added to the payload.

### Follow-ups

The first call captures the thread id from the `--json` event stream by iterating events
until one has `type == "thread.started"`, then reading its `thread_id`. It is stored
alongside the Claude session id.

On `0.148.0-alpha.9` that event happens to arrive first (§2), but position is an
observation, not a protocol guarantee — the parser must not depend on it. Follow-ups resume that exact thread:

```
codex exec -m gpt-5.6-sol -s read-only --ignore-user-config resume <THREAD_ID> - < followup.md
```

`--last` is never used: with another Codex session open, a second Claude window, or a
concurrent review in another repo, it can attach the follow-up to an unrelated
conversation. Flag placement is load-bearing and verified — `resume` accepts no
`--sandbox` of its own, so the sandbox flag must precede the subcommand.

## 8. Deferred: MCP server

Cut from v1. Codex does expose `codex` and `codex-reply` over stdio MCP (§2), so this is
a scoping decision rather than a limitation.

The reasoning: MCP adds a third execution path with its own lifecycle and thread
bookkeeping, and its `prompt` parameter must be populated from Claude's context — meaning
a payload that the CLI streams from disk instead consumes context window, roughly 15k
tokens for a full transcript. Its one genuine advantage, threaded follow-up, disappears
once exact thread ids are captured, since CLI `resume` then does the same job.

Revisit only if a concrete workflow emerges that the CLI cannot serve.

## 9. Response handling

GPT's response is printed verbatim in a quoted block. Claude then goes finding by
finding: **Agree** / **Disagree, because…** / **Need to check first**. Then it stops.
Nothing is modified without the user's word. The `superpowers:receiving-code-review`
skill is invoked to guard against performative agreement.

**GPT output is untrusted data, not instructions.** Codex reads the repository, so
repository contents can reach GPT's output. If a response contains directives ("run
this", "delete that"), they are surfaced to the user, never executed.

## 10. Privacy

Two distinct exposure surfaces, previously conflated.

### 10.1 Conversation (the `askgpt` path)

Uploads conversation content to OpenAI, which contains more than users picture — file
contents, paths, pasted material.

- Payload written to disk before sending, so it can be inspected.
- `--dry-run` builds the payload and sends nothing.
- Secret scan (`sk-`, `ghp_`, `AKIA`, bearer tokens) **halts and asks** on a hit rather
  than scrubbing, because silent scrubbing teaches false confidence.

Absent a scan hit, sending proceeds without an extra prompt: the goal is to remove
friction, not relocate it.

### 10.2 Repository (both paths)

Codex reads the working tree directly. **`read-only` means Codex cannot modify the repo;
it does not mean Codex cannot read it.** So `.env` files, gitignored config, test
fixtures with real credentials, and local key material are all in scope even when they
never appear in `payload.md`. The README states this explicitly — the phrase "read-only
sandbox" otherwise reads as a confidentiality guarantee, which it is not.

Preflight therefore scans the **entire Codex-readable working tree** for sensitive
filenames — not the review target — and warns before dispatch. Scanning the diff would be
incoherent with this whole section: a gitignored `.env` is precisely the file Codex can
read and the diff will never contain.

- Scope: the full repository tree, **regardless of Git tracking or ignore status**.
- Excluded: `.git/` only.
- Matched: `.env*`, `*.pem`, `*.key`, `id_rsa*`, `*.p12`, `*.pfx`, `credentials.json`,
  `.npmrc`, `.netrc`, `.aws/`, `*.keystore`.
- Filename matching only. Content scanning of an entire tree is too slow to sit in front
  of every review, and the filename signal is what makes the warning actionable.

**Not shipping an `.askgptignore`.** It was considered and rejected: Codex explores the
repository through its own sandbox, which has no per-file exclusion mechanism, so such a
file could only *request* that Codex skip a path. An advisory boundary that presents as
enforcement is worse than no boundary, because it invites reliance. Revisit if Codex
gains real path exclusion.

### 10.3 Artefacts on disk

Payload and response files are written to a `0700` directory with `0600` files, and
removed after the run unless `--keep` is passed.

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
- **Read-only tool regression test** (live, manual): confirm Sol under `-s read-only` can
  still run `git status --porcelain` and read a file. This was broken in Codex in July
  2025 (openai/codex#31843), where `read-only` left Sol with no tools while
  `workspace-write` worked. It is fixed on `0.148.0-alpha.9` (§2), but the failure is
  silent and would gut the tool, so it is worth a standing check after Codex upgrades.
- **Model fail-closed test**: with `gpt-5.6-sol` forced unavailable, the command must
  error rather than fall back to `gpt-5.6-terra`.
- **Thread-id test**: capture from `--json`, resume by exact id, assert continuity;
  assert `--last` is never invoked.

## 13. Out of scope

- Any use of the OpenAI API or an API key.
- Automatic application of GPT's suggestions.
- Reviewers other than Codex/GPT.
- CI integration.
- MCP server (see §8 — deferred, not rejected).
- `.askgptignore` (see §10.2 — unenforceable today).
