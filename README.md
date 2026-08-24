# ask-gpt

Ask GPT from inside Claude Code. Adversarial code review and second opinions through
your existing ChatGPT subscription — no API key, no second bill.

> **Status: design stage.** The spec is complete and the underlying plumbing is
> verified working, but the commands are not built yet. See
> [the design spec](docs/superpowers/specs/2026-08-23-ask-gpt-design.md).

## Privacy — read this first

The `askgpt` command **uploads your Claude Code conversation to OpenAI.** Conversations
contain more than people picture: file contents, paths, and anything that was pasted in.

Protections built in:

- The exact payload is written to disk before anything is sent, so you can read it.
- `--dry-run` builds the payload and sends nothing.
- A secret scan (`sk-`, `ghp_`, `AKIA`, bearer tokens) **halts and asks** on a hit
  rather than scrubbing silently.

The `gptreview` command sends your code and diff, not your conversation.

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
  attached verbatim.

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
- A 238 KB session transcript reduces to 17 KB once tool-result payloads and
  system-reminder blocks are dropped, which is what makes sending it verbatim practical.
- No environment variable exposes the Claude session id;
  `CLAUDE_CODE_HOST_SESSION_ID` is a different identifier.

Full detail in the [design spec](docs/superpowers/specs/2026-08-23-ask-gpt-design.md).

## License

MIT
