---
name: second-opinion
description: Use when the user wants an independent second opinion or adversarial code review from GPT, or says "ask GPT", "ask Sol", "get a second opinion", or "have GPT review this"
---

# second-opinion

Sends work to GPT-5.6-sol through the Codex CLI and relays the response.

Named `second-opinion` rather than `ask-gpt` so it does not sit next to the `/askgpt`
command looking like a near-duplicate of it. This is context for when to reach for the
tool; the commands below are what actually run.

- `/gptreview` — adversarial review of the current change. GPT sees the task and the
  code, never Claude's account of what it did. That independence is the point.
- `/askgpt <question>` — a general question with the session dialogue attached.
- `/gptfollow <pushback>` — continue the same thread to argue with a finding.

Run `askgpt usage` to report remaining plan quota. It reads a local file and makes no
network call, so it never consumes the allowance it reports.

## Reading the response

Relay it verbatim first. Then evaluate each finding on its merits — agree, disagree with
reasoning, or flag it as needing verification. Do not perform agreement, and do not
implement suggestions without the user's word.

## Reporting failures

If the command fails, show the user the actual error. Never retry automatically: retries
consume ChatGPT subscription quota.

If the output carries a NOTE saying the review came from a fallback model, say so
prominently in your reply before relaying anything. A weaker model's opinion must not be
presented as the pinned model's.
