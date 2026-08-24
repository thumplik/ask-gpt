# ask-gpt

Ask GPT from inside Claude Code. Adversarial code review and second opinions using the
Codex access included with your ChatGPT plan — no API key required.

(Usage counts against your ChatGPT plan's limits, which vary by tier.)

> **Status: built and working.** 210 tests, plus 29 end-to-end acceptance checks (`make acceptance`), and the tool has been used to review its
> own implementation. See [the design spec](docs/superpowers/specs/2026-08-23-ask-gpt-design.md)
> and [the implementation plan](docs/superpowers/plans/2026-08-23-ask-gpt.md).

## Whose account does this use?

**Yours, and only yours.** ask-gpt never handles credentials. It shells out to your local
`codex` binary, and Codex resolves authentication from your own `~/.codex` — so everyone
who installs it authenticates themselves and spends their own ChatGPT quota. There is no
shared account, no bundled token, and nothing in this repository grants access to anyone
else's. If you are not logged in, the tool refuses and tells you to run `codex login`.

## Privacy — read this first

**Two separate exposures, worth understanding before you run this.**

**Your conversation.** `askgpt` uploads your Claude Code dialogue to OpenAI.
Conversations contain more than people picture: file contents, paths, and anything that
was pasted in.

**Your filesystem — not just your repository.** This is the part people get wrong, and
the README got it wrong until it was measured.

`read-only` is a **write** restriction. It does not confine reads to the repository.
Verified on Codex 0.148.0-alpha.9: a `-s read-only` run read a file in `$HOME`, outside
the working directory entirely — and it still did so with `sandbox_permissions=[]`. This
tool also passes `--ignore-user-config`, so any narrowing you have configured locally
does **not** apply.

Treat everything your user account can read as reachable: `~/.ssh`, `~/.aws/credentials`,
`~/.config/gh/hosts.yml`, `~/.codex/auth.json`, other repositories, browser profiles.

The preflight scans **the repository only**. It is a useful check on the most likely
place for an accident, not a boundary. There is no setting available to us that makes it
one. If that reach is unacceptable for your machine, run this in a container or a VM.

### What crosses which boundary

There are three, and conflating them is how people misjudge the risk:

| Boundary | What crosses it |
|---|---|
| Your machine → local Codex process | **Anything your user account can read.** Measured, not assumed: `-s read-only` read a file in `$HOME` from a run whose working directory was this repo. |
| Local Codex → OpenAI | Whatever Codex quotes or summarises while reasoning, plus the payload we send. |
| Constrained, not absent | Codex's **agent tools** cannot modify your workspace. Files are still written: payloads, response archives, thread state, and Codex's own session logs — `askgpt usage` reads those logs, so writes demonstrably happen. |

Two consequences, both uncomfortable and both true:

**`read-only` constrains agent writes, not disclosure.** A file Codex reads can be quoted
into its reasoning and uploaded even though we never put it in the payload.

**`--dry-run` shows the initial payload, not everything that may leave.** It is exactly
what we send. Once a real run starts, Codex reads further files and may transmit what it
reads or derives. We cannot enumerate or audit the complete outbound context — nobody in
this position can.

**The repository is not the boundary.** The preflight scans the repository because that
is where accidents most often live, not because reads stop there. They do not.

Protections built in:

- The exact payload is written to disk before anything is sent, so you can read it.
- `--dry-run` builds the payload and sends nothing.
- A secret scan (`sk-`, `ghp_`, `AKIA`, bearer tokens) **halts** on a hit rather than
  scrubbing silently. Override with `--allow-secrets`.
- A preflight scan of the whole working tree **halts** on two different things:
  sensitive *filenames* (`.env`, `*.pem`, `.aws/`, …) and secret-shaped *content* inside
  ordinary files. Both matter, and neither catches the other: filename matching cannot
  see a live key pasted into `config.py`, which Codex reads just as happily. Override
  with `--allow-sensitive-files`. It halts rather than warning because a warning printed
  as the request goes out is not something you can act on.

  Deliberately **no entropy or "looks fake" filter.** Measured on real and fake keys,
  entropy does not separate them (3.7–4.9 vs 4.7–5.0, fully overlapping), and a
  run-length filter would suppress `sk-abcdefghij0123456789ABCD` — which is exactly the
  shape of a placeholder pasted over a real key and forgotten. A filter that usually
  works is worse than none for a security control, so this errs toward noise.

The two overrides cover different boundaries and are deliberately separate:
`--allow-secrets` concerns the **payload we upload**; `--allow-sensitive-files` concerns
**your repository**, which Codex reads directly.

### Known limits

Worth stating plainly rather than leaving you to discover them:

- **The preflight covers the repository, and Codex can read beyond it.** That is the
  largest gap and it is not closable from here — no available Codex setting restricts
  reads. Use a container if that matters for your machine.
- Detection covers the common credential formats (OpenAI, GitHub, AWS, Slack, Google,
  Stripe, npm, PyPI, JWTs, private-key blocks) plus a catch-all that keys on the
  *variable name* — `DATABASE_PASSWORD = "…"` is flagged whatever the value looks like,
  because an unfamiliar format is unrecognisable by shape. A secret in an unusual format
  stored under an unrevealing name can still slip through.
- It skips files over 256 KB, binaries, and vendor directories (`node_modules`, `.venv`,
  `target`, …). A key inside a vendored dependency will not be flagged.
- Discussing credentials trips it. A conversation *about* secret detection contains
  secret-shaped strings — this README does. That is the intended trade: false positives
  you can override beat false negatives you never see.
- Payloads and responses live in a `0700` directory as `0600` files, deleted after each
  run unless you pass `--keep`.

`gptreview` sends your code, not your conversation.

## The problem

Getting GPT to review work done in Claude Code takes four copy-paste steps per round:
copy Claude's output, point ChatGPT at the branch, paste and ask, then paste the
response back. This collapses that to one command.

Because Codex runs against your local working tree, it sees uncommitted work — strictly
more than ChatGPT's GitHub connector, which only sees what you have pushed.

## How it works

```
question / review request
        |
        v
  context packager  -->  payload.md   (written to disk, inspectable)
        |
        v
  codex CLI  (read-only sandbox, your ChatGPT auth)
        |
        v
  relayed verbatim, then rebutted point by point
```

Two entry points over one shared layer:

- **`/gptreview`** — **transcript-blind** adversarial review of your branch or working
  tree. GPT sees your original task and the code, but *not* Claude's account of what it
  did. A reviewer that has read the defendant's summary is not independent.

  The honest label is *transcript-blind review by a second model*, not *independent
  review*. Claude still decides when to run it, what it covers, and how the answer is
  relayed — so it is an advisory channel, not a gate. Two consequences worth knowing:
  Claude's reasoning still reaches the reviewer through code comments, specs and commit
  messages, and every review archives its full text with a length and hash so a
  truncated relay is detectable rather than silent.
- **`/askgpt <question>`** — a general second opinion, with the recent conversation
  attached. Dialogue is passed through word for word; tool payloads are omitted, apart
  from failed command output, which is kept within a size cap because it is usually the
  most useful evidence in a debugging session.

Claude relays GPT's answer word for word, then says which findings it agrees with,
which it thinks are wrong and why, and which need checking. Nothing gets changed
without your say-so.

## Install

```bash
git clone https://github.com/thumplik/ask-gpt
cd ask-gpt
./install.sh
```

The installer symlinks the commands and skill into your Claude config directory and
verifies Codex is present and logged in. It prints the resolved binary and `auth: ok`
when it worked. It refuses to overwrite anything that is not a symlink, so it will not
clobber existing files.

Because it installs into `~/.claude`, the commands are available in **every project on
your machine**, not just this repo. Editing this checkout updates them live — the
install is symlinks, not copies.

## Usage

### The command surface

| | What it is |
|---|---|
| `/gptreview` | Adversarial review of your branch or working tree |
| `/askgpt <question>` | A question, with your recent conversation attached |
| `/gptfollow <pushback>` | Continue the same thread to argue with a finding |
| `/gptusage` | Best-effort plan usage so far. Costs nothing |
| `askgpt accept/risks/unaccept` | Per-project ledger of accepted review findings |
| `askgpt …` | The same thing from a terminal — the installer puts it on your PATH |

There is also a **skill** named `second-opinion`. It is not a command; it is context that
lets Claude reach for these tools when you say "get a second opinion" without typing a
slash command. It is deliberately *not* called `ask-gpt`, so it does not sit next to
`/askgpt` looking like a duplicate of it.

### The everyday loop

Work with Claude as normal. When you want an independent opinion:

```
/gptreview
```

Claude runs the review, then relays GPT's findings **verbatim**, then argues with them
point by point — agree, disagree with reasoning, or needs-checking. Nothing in your code
changes unless you say so.

For a question rather than a review:

```
/askgpt is this retry approach actually sound, or am I papering over a race?
```

That one attaches your recent conversation, so GPT can see what you and Claude have
been doing rather than guessing from the code alone.

### Choosing what gets reviewed

`/gptreview` picks a sensible target on its own: on a feature branch it reviews
everything that would ship if you merged right now — commits since the merge base **plus
your uncommitted work**. On the default branch it reviews uncommitted changes. Override
when you want something specific:

| You type | GPT reviews |
|---|---|
| `/gptreview` | auto: branch-vs-default, or uncommitted work |
| `/gptreview --uncommitted` | staged + unstaged + untracked only |
| `/gptreview --base develop` | everything that would ship if merged into `develop` |
| `/gptreview --commit abc123` | exactly that commit |

The default deliberately includes uncommitted work, because the usual moment for this is
right after Claude writes code and before anything is committed. A tool that reviewed
only commits would review yesterday's work while looking like it succeeded.

### Telling GPT what the job was

By default GPT reviews the code on its own terms. Give it the actual requirement and it
can also catch what is *missing*, not just what is wrong:

```
/gptreview --task "add retry with backoff to the uploader, must not retry on 4xx"
/gptreview --task-file docs/specs/uploader.md
```

If it cannot find an authoritative task it says so and reviews correctness only — it
never guesses what you meant. Wrong context produces confident, misdirected findings;
missing context does not.

### Useful flags

| Flag | Effect |
|---|---|
| `--dry-run` | Build the payload, print it, send nothing. Free. |
| `--keep` | Keep the payload and response files, and print where. |
| `--model <slug>` | Override the pinned model. Warns loudly, since the point is Sol's judgement. |
| `--no-fallback` | Fail outright instead of falling back to a weaker model. |
| `--allow-secrets` | Proceed past the secret scan (for false positives). |
| `--allow-sensitive-files` | Proceed past the sensitive-file halt. |

There is also `askgpt usage`, which takes no flags and makes no network call.

**Start with `--dry-run` the first time.** It shows the initial payload ask-gpt will
send, and costs nothing. It is not a complete disclosure list: a live Codex run may
subsequently read and transmit additional context.

### If the pinned model is unavailable

Reviews attempt `gpt-5.6-sol`, then `gpt-5.6-terra` where permitted. **Both may be
unavailable to you** — availability depends on your plan, workspace policy, region and
Codex version, and managed Business/Edu/Enterprise workspaces can restrict models or
local Codex entirely. When a fallback happens the run continues rather than failing — and says so
unmissably:

```
====================================================================
NOTE: gpt-5.6-sol was unavailable. This review is from gpt-5.6-terra,
      a weaker model. Weigh it accordingly, and re-run later for gpt-5.6-sol.
====================================================================
```

Pass `--no-fallback` to fail instead. Only an unavailable *model* advances the chain —
quota, transport and auth failures still stop at the first attempt, because retrying
those would spend real quota and would not help. Trying another model costs nothing: an
unavailable slug is rejected before inference.

### Checking your plan usage

```
/gptusage
```

or from any terminal:

```bash
askgpt usage
```

```
Plan:   plus
Used:   2% of the 7-day window
Resets: 2026-08-30 20:35
```

**Best-effort, not authoritative.** It reads an undocumented local Codex session record,
reports the `primary` window only, and may miss other allowances or credit pools. It
reports what has been *used*, not what remains. Treat Codex's own `/status` or the OpenAI
usage dashboard as authoritative.

It costs nothing: the figure is read from disk rather than requested — checking your remaining
allowance should not consume it. The reading is only as fresh as your last Codex run,
which the output says plainly, and every review refreshes it.

Each review also prints a one-line `quota: N% of the plan window used` footer, so you
see the trend without asking.

### Project memory: two different kinds, on purpose

`/askgpt` and `/gptfollow` share **one conversation per repository** (keyed on the git
root, so asking from a subdirectory continues the same thread). Ask something today,
follow up from a different Claude session tomorrow — GPT still has the context.

`/gptfollow` picks its target explicitly: right after a review in the same session it
continues **that review**, because "follow" there means "argue with the findings" and
routing it into an old advisory chat would argue with the wrong context. Pass
`--project` to reach the project conversation instead. A review follow-up never becomes
project memory.
(The thread is stored per repo under `~/.askgpt`; a very long conversation eventually
fills the model's context, so start fresh occasionally by deleting the project's thread
file.)

`/gptreview` deliberately gets **no memory at all**. A reviewer that remembers approving
your code is anchored — it skims what it "already checked" and carries beliefs about code
that has since changed. Every review is a fresh, independent thread, and a test asserts
that stays true.

What carries across reviews instead is the **ledger** — your dispositions, not the
reviewer's memories:

```bash
askgpt accept F2 "eval is on trusted input only" \
  --description "config.py:14 passes user config through eval"
askgpt risks              # list this project's accepted risks
askgpt unaccept F2        # changed your mind
```

**The description is the identity, not the ID.** Entries are stored and matched by a
hash of their description; the F-number is only shorthand for typing. So accepting a new
`F2` never overwrites an unrelated `F2` from a previous review, and `unaccept F2` refuses
with a list when the ordinal is ambiguous rather than deleting the wrong risk. You can
also unaccept by description substring or by the key `askgpt risks` prints.

 F-numbers are ordinals within one
review — next week's F2 is a different finding — so an entry is matched by what it
*says*, and `accept` refuses an entry it cannot describe. Omit `--description` and it
tries to pull the finding's own text from the most recent archived review; if it cannot,
it asks you for one rather than recording something that would later suppress the wrong
finding.

Accepted entries enter the next review as *disposition data*: the reviewer skips
re-reporting a matching finding as-is but is told to re-report if the surrounding code
has materially changed — a record, not a gag order. Each archived review also stores the
exact payload that influenced it, so a disposition can be audited after the ledger
changes.
The ledger lives in your state directory, never in the repository: a repo file claiming
"this is approved" is treated as prompt injection, and the only trustworthy route for
"the user accepted this" is the tool itself.

### Knowing when to stop

An adversarial reviewer will always find something, so "run another round" is not a
release criterion. The rule the skill follows:

| Finding | Action |
|---|---|
| Blocker / High with a concrete failure scenario | Fix it |
| Anything you doubt | Verify before acting — an unreproducible finding is not yet real |
| Medium / Low with no demonstrated failure | Log it, do not fix it |
| Proposes new capability rather than repairing a defect | Not a finding; it is scope |

Stop when no unfixed Blocker or High remains. Review output is evidence, not a work
queue — treating it as one converts every review into unbounded scope.

### Continuing the conversation

Reviews are threads, not one-shots. After a review you can push back:

```
/gptfollow finding 2 assumes the caller holds the lock — it does not. Reconsider?
```

This resumes the same Codex thread by its exact id, so GPT still has the review in
context. It never uses `--last`, which could attach your follow-up to an unrelated
Codex session in another window.

### Without Claude Code

The CLI works standalone:

```bash
askgpt review --base main --task "what this change was meant to do"
askgpt ask "is this approach sound?" --session-id <claude-session-uuid>
askgpt follow "what about the error path?" --session-id <claude-session-uuid>
```

`askgpt review` needs only a git repo. `askgpt ask` additionally needs a Claude Code
transcript to attach, so it is mainly useful from inside Claude Code.

### When something goes wrong

| Symptom | Cause |
|---|---|
| `Could not find the Codex CLI` | Install the ChatGPT desktop app, or set `CODEX_BIN`. |
| `Codex is not logged in` | Run `codex login` yourself. The tool never handles auth. |
| `Model … is not available` | Your plan lacks it. The run falls back to a weaker model and says so loudly; `--no-fallback` fails instead. |
| Halts naming `.env` or similar | Working as designed. Move the file, or pass `--allow-sensitive-files`. |
| `No transcript at …` | `ask` needs a real Claude session id. Use `review` instead, which needs none. |
| `No staged, unstaged, or untracked changes` | Nothing to review. Commit first and use `--base`, or `--commit HEAD`. |

It never retries on failure. Retries would silently spend your ChatGPT quota, so every
error stops and tells you what happened.

`make test` runs the full suite — 210 tests, plus 29 end-to-end acceptance checks (`make acceptance`), no dependencies to install.

## Supported platforms

| Platform | Status |
|---|---|
| macOS | Tested, including live runs against a real account |
| Linux | CI-tested (unit + acceptance) with a **stubbed** Codex. The live auth and inference path is **not** yet verified on Linux |
| WSL | Untested. Codex and Claude Code must be authenticated *inside* the same WSL environment; a Windows desktop install does not satisfy that |
| Native Windows | **Unsupported.** The installer needs Bash, Unix symlinks and Unix permissions |

Passing Ubuntu CI proves the Python and installer are portable. It does not prove a real
Linux authentication and inference path — those are different claims and only the first
is evidenced.

## Requirements

- macOS with the ChatGPT desktop app (bundles the Codex CLI), or Codex installed
  separately
- A ChatGPT plan that includes Codex
- `codex login` completed — `codex login status` should report a ChatGPT account
- Claude Code

Also needed: Python 3.9+, Git, and a Unix shell — all present by default on macOS and
Linux. Nothing to `pip install`.

The [superpowers](https://github.com/anthropics/claude-plugins-official)
plugin is **optional**: `/gptreview` uses its `receiving-code-review` skill when present
and falls back to equivalent inline instructions when not, so a clean install works
without it.

## Design notes

A few findings from building this, verified rather than assumed:

- The Codex CLI ships inside the ChatGPT app bundle and is **not on `PATH`**.
- Model slugs are account-tier dependent. `gpt-5.6-sol` works; `gpt-5.6`,
  `gpt-5.6-codex`, and `gpt-5.3-codex` are all rejected with HTTP 400. Invalid slugs
  cost no quota — they are rejected before inference.
- A 238 KB session transcript reduces to 17 KB once system-reminder blocks and
  successful tool-result payloads are dropped, which is what makes attaching the dialogue
  practical.
- No environment variable exposes the Claude session id;
  `CLAUDE_CODE_HOST_SESSION_ID` is a different identifier.
- `codex review` looks purpose-built for this and is not usable: its `--base` /
  `--commit` / `--uncommitted` flags are mutually exclusive with a custom prompt, and it
  exposes no `-m`, so it cannot pin a model. Both paths use `codex exec` instead.
- `codex exec resume` takes no `--sandbox` of its own — the flag must precede the
  subcommand. Thread ids come from the `--json` stream and are resumed explicitly;
  `--last` is never used, since it can attach to an unrelated Codex session.

Full detail in the [design spec](docs/superpowers/specs/2026-08-23-ask-gpt-design.md).

## Data this tool stores

Under `~/.askgpt` (override with `$ASKGPT_STATE_DIR`): Codex thread ids, and the full
text of the last 50 reviews at `0600` (older ones pruned automatically). Payloads live in
a `0700` temp directory and are deleted after each run unless `--keep` is passed or a
secret-scan halt preserves one for inspection. Codex writes its own session logs under
`~/.codex/sessions` independently.

Remove everything ask-gpt has stored with `rm -rf ~/.askgpt`.

See [SECURITY.md](SECURITY.md) for the threat model, what counts as a mitigation versus
an actual boundary, and guidance for organisational use.

## Status

**Public beta.** The engineering is solid and the review loop has found real defects in
its own implementation repeatedly. What holds it back from an unqualified v1 is release
contract rather than code: the live path is verified on macOS only, Codex compatibility
is pinned to one tested build rather than a supported range, and several behaviours
depend on undocumented Codex internals that a future release may move.

Install it, use it, and read [the privacy section](#privacy--read-this-first) first.

## License

MIT
