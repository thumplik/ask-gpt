# ask-gpt

Ask GPT from inside Claude Code. Adversarial code review and second opinions using the
Codex access included with your ChatGPT plan — no API key required.

(Usage counts against your ChatGPT plan's limits, which vary by tier.)

> **Status: built and working.** 173 tests, and the tool has been used to review its
> own implementation. See [the design spec](docs/superpowers/specs/2026-08-23-ask-gpt-design.md)
> and [the implementation plan](docs/superpowers/plans/2026-08-23-ask-gpt.md).

## Privacy — read this first

**Two separate exposures, worth understanding before you run this.**

**Your conversation.** `askgpt` uploads your Claude Code dialogue to OpenAI.
Conversations contain more than people picture: file contents, paths, and anything that
was pasted in.

**Your repository.** Both commands let Codex read your working tree. **`read-only` means
Codex cannot *modify* your repo — it does not mean Codex cannot *read* it.** Gitignored
files, `.env`, local credentials, and test fixtures are all readable. A preflight warns
on obvious sensitive files, but treat the whole working tree as in scope.

Protections built in:

- The exact payload is written to disk before anything is sent, so you can read it.
- `--dry-run` builds the payload and sends nothing.
- A secret scan (`sk-`, `ghp_`, `AKIA`, bearer tokens) **halts** on a hit rather than
  scrubbing silently. Override with `--allow-secrets`.
- A preflight scan of the whole working tree **halts** if it finds sensitive files
  (`.env`, `*.pem`, `.aws/`, …). Override with `--allow-sensitive-files`. It halts
  rather than warning because a warning printed as the request goes out is not
  something you can act on.
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

- **`/gptreview`** — adversarial review of your branch or working tree. GPT sees your
  original task and the code, but *not* Claude's account of what it did. A reviewer that
  has read the defendant's summary is not an independent reviewer.
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
| `--allow-secrets` | Proceed past the secret scan (for false positives). |
| `--allow-sensitive-files` | Proceed past the sensitive-file halt. |

There is also `askgpt usage`, which takes no flags and makes no network call.

**Start with `--dry-run` the first time.** It shows you exactly what would leave your
machine and costs nothing.

### Checking how much quota you have left

```
askgpt usage
```

```
Plan:   plus
Used:   2% of the 7-day window
Resets: 2026-08-30 20:35
```

This costs nothing. Codex writes a rate-limit record into its own session log on every
run, so the figure is read from disk rather than requested — checking your remaining
allowance should not consume it. The reading is only as fresh as your last Codex run,
which the output says plainly, and every review refreshes it.

Each review also prints a one-line `quota: N% of the plan window used` footer, so you
see the trend without asking.

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
| `Model … is not available` | Your plan lacks that model. It fails closed rather than quietly downgrading. |
| Halts naming `.env` or similar | Working as designed. Move the file, or pass `--allow-sensitive-files`. |
| `No transcript at …` | `ask` needs a real Claude session id. Use `review` instead, which needs none. |
| `No staged, unstaged, or untracked changes` | Nothing to review. Commit first and use `--base`, or `--commit HEAD`. |

It never retries on failure. Retries would silently spend your ChatGPT quota, so every
error stops and tells you what happened.

`make test` runs the full suite — 173 tests, no dependencies to install.

## Requirements

- macOS with the ChatGPT desktop app (bundles the Codex CLI), or Codex installed
  separately
- A ChatGPT plan that includes Codex
- `codex login` completed — `codex login status` should report a ChatGPT account
- Claude Code

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

## License

MIT
