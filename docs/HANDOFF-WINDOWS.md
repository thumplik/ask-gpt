# Handoff: Windows support

You are picking this up on a Windows machine. That is the point — nobody has ever
run this on Windows, and every serious bug in this project so far was found by
running the real thing, never by reasoning about it.

Read this whole file first. It is short, and it will save you rediscovering things
that cost real time.

## What ask-gpt is

A CLI that lets Claude Code send work to GPT (via OpenAI's Codex CLI, using the
user's ChatGPT plan — no API key) for an independent adversarial code review, and
relays the response back. Four slash commands (`/gptreview`, `/askgpt`,
`/gptfollow`, `/gptusage`) plus a ledger of accepted risks.

Start with `README.md` for behaviour and `SECURITY.md` for the threat model.
`docs/superpowers/specs/2026-08-23-ask-gpt-design.md` records *why* decisions were
made, including several that were reversed and why.

## Your goal

Make native Windows work, or establish with evidence that it should not, and make
the README's claim accurate either way. Today the README says "Unsupported — use
WSL," which is honest but was never tested.

**WSL is expected to work already** and is worth confirming first: it is a real
Unix environment, so everything should apply. Confirming WSL is a cheap win and
tells you whether the problem is "Windows" or "not-Unix."

## The four blockers, with evidence

Verified on macOS; you should re-verify each on Windows rather than trusting me.

### 1. `fcntl` — fatal, already handled defensively
`askgpt/ledger.py:35` imports `fcntl` in a `try/except ImportError`, so the module
loads anywhere. `_locked()` then raises a clear `AskGptError` naming the platform
problem. **Nothing crashes; it refuses.** Confirm with:

```
python -c "import askgpt.ledger as L; print(L.fcntl)"
```

`None` on Windows is expected. The Windows equivalent is `msvcrt.locking`. If you
wire it up, the lock must cover the whole read-modify-write cycle — see the comment
on `_locked`; 30 concurrent accepts collapsed to 5 before locking existed.

### 2. POSIX file modes — the real reason for "unsupported"
`0600`/`0700` appear at `askgpt/artifacts.py:17,21`, `askgpt/state.py:46,111,120`,
`askgpt/ledger.py:96,137`. These protect payloads (your conversation), response
archives (full review text) and thread state.

`os.chmod` exists on Windows but the POSIX bits are largely ignored. **This is the
thing that makes a half-working Windows build worse than none:** it would look fine
while storing conversations unprotected. If you support Windows, you need real ACLs
(`icacls`, or `pywin32`) — and a test that *proves* the file is not world-readable,
not merely that `chmod` was called. See `tests/test_artifacts.py` for the shape.

### 3. Installer — bash and symlinks
`install.sh` is bash (`CLAUDE_DIR` at line 5, `ln -s` at line 21, `ASKGPT_BIN_DIR`
at line 63). It symlinks commands into the Claude config dir and the CLI onto PATH.
Windows symlinks need Developer Mode or admin. A PowerShell port is straightforward;
keep the two safety properties it already has:

- it validates **every** destination before mutating any (a partial install is bad)
- it refuses to replace anything that is not a symlink

### 4. Is Codex even available? — unknown, resolve this FIRST
`askgpt/codex.py:19` resolves the binary from a list that is macOS-centric. Nobody
has checked whether the Windows ChatGPT desktop app bundles `codex` at all.

**Do this before any porting work.** If Codex is not obtainable on Windows, the
answer is "use WSL," you document it with evidence, and you are done in an hour.

```
where codex
codex --version
codex login status
```

Note `login status` writes to **stderr** with empty stdout — see the trap below.

## How this project works (please keep to it)

These are not style preferences. Each came from a real defect.

**Verify, never assume.** Every environment claim in the spec has a "how confirmed"
column. When you write "Windows does X," have run something that shows it.

**Never merge streams while establishing behaviour.** `2>&1` hid a bug for this
entire project: `codex login status` writes to stderr, the check read stdout, and
every logged-in user was reported as logged out. Capture stdout, stderr and exit
status separately.

**Mutation-test every fix.** After making a test pass, deliberately break the code
it covers and confirm the test fails. Several tests here passed against broken
implementations until this was done — including one asserting sorting that passed
with the sort removed.

**Run the real thing.** 250+ unit tests never caught the auth bug, the quota
false-positive, or the orphaned threads. `make test` is necessary and not
sufficient. `make acceptance` runs a fresh clone, install, and a run from a
different repo against a stubbed Codex — extend it for Windows rather than
inventing a new harness.

**Do not weaken a test to make it pass.** If a test looks wrong, say so and explain
why. Tests here have been wrong twice; both times it was reported, not quietly
adjusted.

**Upgrade paths are untested territory.** Two bugs today lived only on machines with
prior state — thread files orphaned by a rename, ledgers written before a schema
change. If you change a filename or format, migrate it and test the migration.

## Definition of done

- `make test` and `make acceptance` pass on Windows, or the acceptance suite has a
  documented, justified Windows variant
- CI matrix in `.github/workflows/ci.yml` includes `windows-latest` and is green
- File-permission tests **prove** protection on Windows rather than asserting a
  `chmod` call happened
- `README.md` platform table and `SECURITY.md` reflect what you actually verified
- A migration exists if any on-disk name or format changed

If you conclude Windows should stay unsupported, that is a perfectly good outcome —
but land the evidence in the README so the next person does not re-litigate it.

## Traps found the hard way

- `make test` printing `Ran 272 tests` does **not** mean it passed. Check for `OK`.
- The acceptance suite clones the repo, so it tests **committed** state, not your
  working tree. Commit before running it.
- `timeout` is not present in every shell used here; do not rely on it.
- Prompts phrased as security research can trip OpenAI's content filter and return
  a structured error. The tool handles this correctly — rephrase, do not retry.
- Reviews cost real quota. `--dry-run` is free and shows the payload. `askgpt usage`
  is also free.

## Working with the tool on itself

Once installed, use it: `/gptreview --task "add Windows support"`. It has found real
defects in its own implementation repeatedly, including several the same day they
were written. When it reports findings, the stopping rule in `SKILL.md` applies —
fix Blocker/High with a concrete failure scenario, log the rest, and do not treat
review output as an automatic work queue.
