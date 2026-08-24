# Security

## Reporting

Open a GitHub issue. This is a personal project with no SLA — if the issue is sensitive,
say so briefly and ask for a private channel before pasting details.

## The threat model, stated plainly

ask-gpt launches an AI agent that reads your filesystem while processing repository
content it does not control. Two hazards combine:

**Reads are not confined to the repository.** Codex's `-s read-only` prevents its agent
tools from *writing*. It does not stop them reading. Measured on `0.148.0-alpha.9`: a
read-only run whose working directory was a repository read a file in `$HOME`, and still
did so with `sandbox_permissions=[]`. ask-gpt also passes `--ignore-user-config`, so
narrowing you configured locally does not apply. Treat everything your user account can
read as reachable.

**Repository content can influence the reviewer.** Codex reads source, comments, specs
and test fixtures before you see any output. Text in those files that addresses the
reviewer is a prompt-injection vector. The persona is hardened against it — repository
content is declared evidence and never instructions, and reviewer-directed text is itself
a Blocker finding — but prompt hardening is mitigation, not a boundary.

Combined, the realistic worst case is repository content steering the agent toward files
elsewhere on your machine.

## What is and is not a control

| | |
|---|---|
| Mitigation | Secret scanning, repository preflight, prompt hardening, inspectable payloads, `--dry-run` |
| **Not a boundary** | All of the above. They reduce the chance of an accident; they cannot prevent a determined one |
| **Actual boundary** | OS-level isolation — a container, VM, or dedicated low-privilege account holding only the repository and credentials the run needs |

If you are working with material where disclosure would be serious, use the actual
boundary. Do not rely on the mitigations.

## Organisational use

Sending proprietary code to a separately authenticated AI service is usually a decision
your organisation needs to make, not one an individual developer makes by installing a
tool. Get approval first.

## Data at rest

ask-gpt writes to `$ASKGPT_STATE_DIR` (default `~/.askgpt`):

- `threads/` — Codex thread ids, one small JSON file per session
- `responses/` — full review text, `0600`, newest 50 kept, older pruned automatically

Payloads go to a `0700` temp directory and are deleted after each run unless `--keep` is
passed or a secret-scan halt preserves them for inspection. Codex separately writes its
own session logs under `~/.codex/sessions` on every run; `askgpt usage` reads those.

To remove everything ask-gpt has stored: `rm -rf ~/.askgpt`
