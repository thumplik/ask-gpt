# ask-gpt

Ask GPT from inside Claude Code. Adversarial code review and second opinions using the
Codex access included with your ChatGPT plan — no API key required.

(Usage counts against your ChatGPT plan's limits, which vary by tier.)

> **Status: design stage.** The spec is complete and the underlying plumbing is
> verified working, but the commands are not built yet. See
> [the design spec](docs/superpowers/specs/2026-08-23-ask-gpt-design.md).

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
- A secret scan (`sk-`, `ghp_`, `AKIA`, bearer tokens) **halts and asks** on a hit
  rather than scrubbing silently.
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
