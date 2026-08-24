---
name: second-opinion
description: Use when the user wants an independent second opinion or adversarial code review from GPT, or says "ask GPT", "ask Sol", "get a second opinion", or "have GPT review this"
---

# second-opinion

Sends work to GPT-5.6-sol through the Codex CLI and relays the response.

Named `second-opinion` rather than `ask-gpt` so it does not sit next to the `/askgpt`
command looking like a near-duplicate of it. This is context for when to reach for the
tool; the commands below are what actually run.

- `/gptreview` — **transcript-blind** adversarial review by a second model. GPT sees the
  task and the code, never Claude's account of what it did.

  Call it transcript-blind, not independent. Claude still chooses when to run it, what
  target it covers, what task statement it gets, and how the response is relayed and
  triaged. It is a second-model advisory channel, not an independent review gate. Do not
  tell the user an "independent review passed"; say a second model reviewed the code and
  report what it found.
- `/askgpt <question>` — a general question with the session dialogue attached.
- `/gptfollow <pushback>` — continue the same thread to argue with a finding.

Run `askgpt usage` for a best-effort snapshot of plan usage. It reads a local Codex
session record and makes no network call, so it never consumes the allowance it reports.
Report it as usage so far, never as remaining allowance -- it covers one window and may
miss other allowances or credit pools. Codex's own `/status` is authoritative.

## When to stop

Review output is evidence, not a work queue. Without a stopping rule this loop runs
forever, because an adversarial reviewer will always find something.

- **Fix** Blocker and High findings that come with a concrete failure scenario.
- **Verify first** anything you doubt. A finding you cannot reproduce is not yet real.
- **Log, do not fix** Medium and Low findings without a demonstrated failure, and any
  finding that proposes new capability rather than repairing a defect.
- **Stop** when no unfixed Blocker or High remains. Do not run another round to see what
  else turns up — that is not a release criterion, it is an open-ended search.

Never treat "implement all findings" as automatic. Say which you are acting on and why,
and get the user's agreement before acting on the rest.

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
