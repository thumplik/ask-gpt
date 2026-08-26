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
- `responses/` — full review text, owner-only, newest 50 kept, older pruned automatically

Payloads go to an owner-only temp directory and are deleted after each run unless
`--keep` is passed or a secret-scan halt preserves them for inspection. Codex separately
writes its own session logs under `~/.codex/sessions` on every run; `askgpt usage` reads
those.

**"Owner-only" is enforced differently per platform, and the difference matters.**

On macOS and Linux it is the POSIX mode: `0600` for files, `0700` for directories.

On Windows those modes do nothing. Measured on Windows 10 19045: `os.chmod(path, 0o600)`
leaves the file at `0666`, and neither `os.open(..., 0o600)` nor `tempfile.mkstemp`
applies the mode — a file created in a permissive directory inherits it and grants
`NT AUTHORITY\INTERACTIVE`, meaning any logged-on user, `Modify`. Protection there is
therefore a real ACL restricted to the owning account, applied to both directories and
files. Two consequences worth stating plainly:

- A state directory created by an earlier build inherited whatever its parent allowed.
  It is repaired in place the next time ask-gpt writes to it, not left as it was.
- Because a Windows file is otherwise only as protected as its directory, pointing
  `ASKGPT_STATE_DIR` somewhere world-writable is materially riskier than on POSIX,
  where the mode would still protect the file. The tool re-restricts what it creates,
  but it does not audit a directory you hand it.

The tests assert the resolved ACL rather than that a `chmod` call happened — on Windows
the latter passes against a world-readable file and proves nothing.

To remove everything ask-gpt has stored: `rm -rf ~/.askgpt` (PowerShell:
`Remove-Item -Recurse -Force $env:USERPROFILE\.askgpt`)
