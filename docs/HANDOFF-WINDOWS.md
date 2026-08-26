# Handoff: Windows support

**Status: done. Native Windows is supported.** This file was written before the
work and is kept as the record of what was predicted versus what was true, because
three of the four predictions were wrong in instructive ways.

Verified on Windows 10 Pro 19045, Python 3.13.7, `codex-cli 0.149.0-alpha.4.3`:
279 unit tests pass (3 skipped, each printing why), and the 34-check acceptance
suite passes against a fresh clone, a real install, and a stubbed Codex.

```powershell
.\install.ps1        # needs Developer Mode; refuses with instructions otherwise
.\make.ps1 test
.\make.ps1 acceptance
```

## What ask-gpt is

A CLI that lets Claude Code send work to GPT (via OpenAI's Codex CLI, using the
user's ChatGPT plan — no API key) for an independent adversarial code review, and
relays the response back. Four slash commands (`/gptreview`, `/askgpt`,
`/gptfollow`, `/gptusage`) plus a ledger of accepted risks.

Start with `README.md` for behaviour and `SECURITY.md` for the threat model.

## The four blockers, and what was actually the case

### 1. `fcntl` — resolved, and the prediction was right
`msvcrt.locking` now backs `askgpt/secfs.py`, which is the single place either
platform's locking and permissions come from. The refusal is keyed on the
*capability*, not the platform name, so a host providing neither backend still
fails with an explanation.

Mutation-tested against the original defect: **30 concurrent `accept` processes
leave 6 entries with the lock disabled and 30 with it enabled**, reproducing the
"collapsed to 5" this file described. Windows also fails *worse* than POSIX when
unlocked — `os.replace` raises `WinError 5` because another process holds the
file — so it loses entries *and* crashes.

### 2. POSIX file modes — resolved, but this file had it partly wrong
The claim was that a Windows build "would look fine while storing conversations
unprotected." What is actually true is more specific, and the difference is what
makes it testable:

| Call | Windows behaviour |
|---|---|
| `os.mkdir(mode=0o700)` | **Honoured.** Produces a real restrictive ACL |
| `os.chmod(path, 0o600)` | Ignored. Measured: the file stays `0666` |
| `os.open(..., 0o600)` | Ignored. The file inherits from its directory |
| `tempfile.mkstemp` + `chmod` | Ignored. Same inheritance |

So a Windows file is exactly as protected as its directory, and no more. In a
permissive parent both file recipes produced files granting
`NT AUTHORITY\INTERACTIVE` — any logged-on user — `Modify`.

Two traps this created, both of which cost time:

- **`icacls /inheritance:r /grant:r` does not remove an explicit ACE for another
  principal.** It drops *inherited* entries and replaces the grant for the SID
  named; a directory an older build left explicitly granting `Everyone` survived
  both. The surviving principals have to be enumerated and removed individually.
  Caught only by the upgrade-path test.
- **A protection test written the obvious way proves nothing.** `mkdtemp` returns
  an owner-only directory on both platforms, so asserting "the payload is
  owner-only" passes against an implementation that does nothing. The tests write
  into a deliberately permissive directory (`tests/permissive.py`) and assert the
  *resolved ACL*. Mutation-verified: reverting to the original code fails them.
  The `test_artifacts.py` pair still does not discriminate, and says so in a
  comment rather than pretending otherwise.

### 3. Installer — resolved, with a trap worth knowing
`install.ps1` keeps both safety properties of `install.sh`: every destination is
validated before any is changed, and nothing that is not a symlink is replaced.

**Do not use `New-Item -ItemType SymbolicLink` in Windows PowerShell 5.1.** It does
not pass `SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE`, so it fails with
"Administrator privilege required for this operation" on an account where
Developer Mode already permits the operation. Measured on 5.1.19041: `New-Item`
failed while `mklink` and Python's `os.symlink` both succeeded on the same account
seconds apart. The first version of the installer told users to enable a setting
they had already enabled. The privilege probe must use the same mechanism the
install uses, or it measures something else.

### 4. Is Codex available? — **yes**, and this was the wrong thing to fear
It resolves without configuration. On this machine the binary was at
`~/.codex/plugins/.plugin-appserver/codex.exe`, and `codex login status` reported
`Logged in using ChatGPT` — on **stderr with empty stdout**, exactly as this file
warned.

Note the inversion on WSL: this file called it "expected to work already" and a
cheap first win. But the ChatGPT desktop app bundles a *Windows* `codex.exe`, which
a WSL environment cannot use directly, so WSL is the harder path rather than the
easy one. Native Windows was the better target.

## Defects found by running it, not by reasoning about it

None of these were in the list above. Every one came from executing the thing.

- **`/askgpt` could never have worked.** `project_dir_for` flattened the cwd with
  `replace("/", "-")`, a no-op on `C:\...`, so the Claude transcript directory was
  never found. It survived because the function had *no tests* and both CLI test
  helpers reimplemented it with the same flaw — they agreed with each other, so the
  suite stayed green. Duplication of a rule is how a bug hides from its own tests.
- **Non-ASCII filenames were corrupted.** `subprocess(text=True)` decodes with the
  locale codepage (cp1252 here) while git emits UTF-8. Verified by codepoint:
  `café.txt` (U+00E9) arrived as U+00C3 U+00A9, and that mangled path went into the
  payload sent to Codex. The encoding is pinned wherever git or Codex output is read.
- **`find_codex` would accept a text file as the Codex binary.** `os.access(X_OK)`
  answers True for any readable file on Windows. It checks `PATHEXT` now.

## How this project works (please keep to it)

These are not style preferences. Each came from a real defect.

**Verify, never assume.** Every environment claim has a "how confirmed" column.
When you write "Windows does X," have run something that shows it. Section 2 above
exists because the original claim was reasoned rather than measured.

**Never merge streams while establishing behaviour.** `2>&1` hid a bug for this
entire project: `codex login status` writes to stderr, the check read stdout, and
every logged-in user was reported as logged out.

**Mutation-test every fix.** After making a test pass, deliberately break the code
it covers and confirm the test fails. Two tests added during this work passed
against a disabled implementation until this was done.

**Run the real thing.** 250+ unit tests never caught the auth bug, the quota
false-positive, or the orphaned threads — nor any of the three defects listed
above. `make test` is necessary and not sufficient.

**Do not weaken a test to make it pass.** If a test looks wrong, say so and explain
why. During this work `test_artifacts.py` was rewritten and silently lost two
tests; they were restored.

**Upgrade paths are untested territory.** State directories created by an earlier
build inherited whatever their parent allowed, so writing to one now repairs it.

## Traps found the hard way

- `make test` printing `Ran 279 tests` does **not** mean it passed. Check for `OK`.
- **Do not pipe `make.ps1 test` through `2>&1`.** unittest writes to stderr, and
  PowerShell 5.1 wraps a native command's stderr in `NativeCommandError` records
  and sets `$?` to false — a fully passing run reads as a failure. Observed here on
  a run that genuinely exited 0.
- The acceptance suite clones the repo, so it tests **committed** state, not your
  working tree. Commit before running it.
- `timeout` is not present in every shell used here; do not rely on it.
- Windows PowerShell 5.1 has no ternary, no `??`, and no `&&`/`||` chain operators.
- A skip is invisible in a green run. Every skip here names the privilege or
  platform limit causing it, and CI prints the whole list.
- Prompts phrased as security research can trip OpenAI's content filter and return
  a structured error. The tool handles this correctly — rephrase, do not retry.
- Reviews cost real quota. `--dry-run` is free and shows the payload. `askgpt usage`
  is also free.

## Remaining gaps

- Three tests skip on a developer machine: two need symlink privilege (they run in
  CI, which is elevated), one needs a filename containing a newline, which the
  Windows filesystem forbids outright. The NUL-splitting that one guards is covered
  by an equivalent awkward-filename case using characters Windows does permit.
- The live authentication and inference path is verified on macOS and now Windows.
  Linux remains CI-only with a stubbed Codex.
- The installed Codex (`0.149.0-alpha.4.3`) is newer than `TESTED_VERSION`
  (`0.148.0-alpha.9`). Dry runs do not touch the undocumented contracts listed in
  `askgpt/codex.py`; a real review does. The version warning fires on every run.
