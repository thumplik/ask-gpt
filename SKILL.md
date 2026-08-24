---
name: ask-gpt
description: Use when the user wants an independent second opinion or adversarial code review from GPT, or says "ask GPT", "ask Sol", "get a second opinion", or "have GPT review this"
---

# ask-gpt

Sends work to GPT-5.6-sol through the Codex CLI and relays the response.

- `/gptreview` — adversarial review of the current change. GPT sees the task and the
  code, never Claude's account of what it did. That independence is the point.
- `/askgpt <question>` — a general question with the session dialogue attached.
- `/gptfollow <pushback>` — continue the same thread to argue with a finding.

## Reading the response

Relay it verbatim first. Then evaluate each finding on its merits — agree, disagree with
reasoning, or flag it as needing verification. Do not perform agreement, and do not
implement suggestions without the user's word.

## Reporting failures

If the command fails, show the user the actual error. Never retry automatically: retries
consume ChatGPT subscription quota. If the model is unavailable, do not substitute a
weaker one — the request was for Sol specifically.
