<#
.SYNOPSIS
    Install ask-gpt on Windows.

.DESCRIPTION
    The PowerShell counterpart to install.sh, and it deliberately keeps that
    script's two safety properties:

      * every destination is validated BEFORE any of them is changed, because a
        partial install that stopped at the fourth target used to leave the
        first three already swapped;
      * nothing that is not a symlink is ever replaced.

    Symlinks are used rather than copies so that editing the repository takes
    effect immediately, exactly as on macOS and Linux. Windows only grants
    symlink creation to elevated processes or to accounts with Developer Mode
    enabled, so this refuses with instructions when it cannot make one, rather
    than silently falling back to copying and leaving a second machine behaving
    differently from the first.

    Written for Windows PowerShell 5.1: no ternary, no null-coalescing, and no
    pipeline chain operators.
#>

$ErrorActionPreference = "Stop"

$Repo = $PSScriptRoot

if ($env:CLAUDE_CONFIG_DIR) { $ClaudeDir = $env:CLAUDE_CONFIG_DIR }
else { $ClaudeDir = Join-Path $env:USERPROFILE ".claude" }

if ($env:ASKGPT_BIN_DIR) { $BinDir = $env:ASKGPT_BIN_DIR }
else { $BinDir = Join-Path $env:USERPROFILE ".local\bin" }


function Test-IsSymlink {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    # LinkType, not the ReparsePoint attribute. Junctions, volume mount points
    # and OneDrive placeholders are all reparse points, so the attribute answers
    # "is this a symlink?" with yes for several things that are not -- and
    # New-Link would then delete a junction somebody deliberately placed there,
    # which is exactly the never-replace-a-non-symlink promise being broken.
    # Measured: a junction reports ReparsePoint=True, LinkType=Junction.
    return ($item.LinkType -eq "SymbolicLink")
}

function Get-LinkTarget {
    param([string]$Path)
    return (Get-Item -LiteralPath $Path -Force).Target
}

function New-SymbolicLink {
    <#
        Deliberately mklink and not `New-Item -ItemType SymbolicLink`.

        Windows PowerShell 5.1 does not pass
        SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE to CreateSymbolicLinkW, so
        New-Item fails with "Administrator privilege required for this
        operation" even on an account where Developer Mode makes the operation
        perfectly legal. Measured on 5.1.19041: New-Item failed while mklink
        and Python's os.symlink both succeeded on the same account, seconds
        apart. Trusting New-Item means telling users to enable a setting they
        already enabled.
    #>
    param([string]$Path, [string]$Target)

    if (Test-Path -LiteralPath $Target -PathType Container) { $flag = "/D " } else { $flag = "" }
    $command = "mklink " + $flag + '"' + $Path + '" "' + $Target + '"'
    $output = cmd /c $command 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "failed to create symlink: $Path -> $Target`n$output"
    }
}

function Test-SymlinkPrivilege {
    # ASKGPT_TEST_NO_SYMLINK simulates an account without the privilege.
    #
    # This is a deliberate test seam, not a leftover. The refusal branch is the
    # first thing an unprepared Windows user meets, and it depends on an ambient
    # OS privilege that cannot be dropped from inside the process. A developer
    # machine with Developer Mode enabled HAS the privilege, and so does an
    # elevated CI runner -- so without this the branch is covered nowhere, and
    # it was: it passed once, by accident, on a machine that happened to lack
    # the privilege, then went silently untested the moment that was fixed.
    if ($env:ASKGPT_TEST_NO_SYMLINK) { return $false }

    # Otherwise probed rather than inferred: Developer Mode, elevation and group
    # policy all feed into this, and the only trustworthy answer is to try it --
    # using the same mechanism the install actually uses, or the probe measures
    # something other than what will happen.
    $probe = Join-Path ([IO.Path]::GetTempPath()) ("askgpt-symlink-probe-" + [guid]::NewGuid().ToString("N"))
    $target = $probe + "-target"
    New-Item -ItemType Directory -Path $target | Out-Null
    try {
        $command = 'mklink /D "' + $probe + '" "' + $target + '"'
        cmd /c $command 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
        (Get-Item -LiteralPath $probe -Force).Delete()
        return $true
    } catch {
        return $false
    } finally {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Assert-Available {
    param([string]$Dest)
    if ((Test-Path -LiteralPath $Dest) -and -not (Test-IsSymlink $Dest)) {
        Write-Error "refusing to replace existing non-symlink: $Dest`nmove or delete it, then re-run."
    }
}

function New-Link {
    param([string]$Source, [string]$Dest)
    if (Test-IsSymlink $Dest) {
        # Remove-Item on a directory symlink can recurse into the target on
        # some hosts; .Delete() removes the link itself and nothing else.
        (Get-Item -LiteralPath $Dest -Force).Delete()
    } elseif (Test-Path -LiteralPath $Dest) {
        Write-Error "refusing to replace existing non-symlink: $Dest"
    }
    New-SymbolicLink -Path $Dest -Target $Source
    if (-not (Test-IsSymlink $Dest)) { Write-Error "failed to create symlink: $Dest" }
    Write-Host "  linked $Dest -> $(Get-LinkTarget $Dest)"
}


if (-not (Test-SymlinkPrivilege)) {
    Write-Error @"
Cannot create symlinks on this account, so ask-gpt cannot be installed.

Enable Developer Mode, which grants symlink creation without elevation:
  Settings > System > For developers > Developer Mode

Then re-run this script from a NEW PowerShell window. Running PowerShell as
Administrator also works.

Copying the files instead is deliberately not offered: the install would go
stale on every edit with nothing to tell you it had, and this machine would
then behave differently from a macOS or Linux one.
"@
}

$Shim = Join-Path $BinDir "askgpt.cmd"

# ---------------------------------------------------------------------------
# Preflight. EVERYTHING that can refuse must refuse here, before the first
# mutation of any kind -- creating a directory included. Ordering this after
# the mkdir calls meant a conflict at the last destination still left new
# directories behind, which is the partial install this check exists to stop.
# ---------------------------------------------------------------------------

@(
    (Join-Path $ClaudeDir "ask-gpt"),
    (Join-Path $ClaudeDir "commands\gptreview.md"),
    (Join-Path $ClaudeDir "commands\askgpt.md"),
    (Join-Path $ClaudeDir "commands\gptfollow.md"),
    (Join-Path $ClaudeDir "commands\gptusage.md"),
    (Join-Path $ClaudeDir "skills\second-opinion")
) | ForEach-Object { Assert-Available $_ }

# The shim is generated rather than linked, so it cannot be held to
# "never replace a non-symlink" -- it is always a regular file. What it is held
# to instead: replace one we wrote, never anything else owning that name.
if ((Test-Path -LiteralPath $Shim) -and -not ((Get-Content -LiteralPath $Shim -Raw -ErrorAction SilentlyContinue) -like "*ask-gpt shim*")) {
    Write-Error "refusing to replace a file this installer did not write: $Shim`nmove or delete it, then re-run."
}

# Checked here rather than at the point of use: python is needed to write the
# shim, and discovering its absence after six links had been replaced would
# report failure while leaving the install half-applied.
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Write-Error "python was not found on PATH; install Python 3 and re-run." }

# ---------------------------------------------------------------------------
# Past this line, mutations begin.
# ---------------------------------------------------------------------------

New-Item -ItemType Directory -Force -Path (Join-Path $ClaudeDir "commands") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ClaudeDir "skills") | Out-Null
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

New-Link $Repo                                        (Join-Path $ClaudeDir "ask-gpt")
New-Link (Join-Path $Repo "commands\gptreview.md")    (Join-Path $ClaudeDir "commands\gptreview.md")
New-Link (Join-Path $Repo "commands\askgpt.md")       (Join-Path $ClaudeDir "commands\askgpt.md")
New-Link (Join-Path $Repo "commands\gptfollow.md")    (Join-Path $ClaudeDir "commands\gptfollow.md")
New-Link (Join-Path $Repo "commands\gptusage.md")     (Join-Path $ClaudeDir "commands\gptusage.md")

# Retire the old skill name: it sat beside /askgpt looking like a duplicate.
# Only ever removes a symlink, and only one pointing back into this repo.
$OldSkill = Join-Path $ClaudeDir "skills\ask-gpt"
if ((Test-IsSymlink $OldSkill) -and ((Get-LinkTarget $OldSkill) -eq $Repo)) {
    (Get-Item -LiteralPath $OldSkill -Force).Delete()
    Write-Host "  removed superseded skill link $OldSkill"
}

# The skill moved from the repo root into plugin layout (skills\second-opinion\
# SKILL.md), so the link targets that directory now. An install made before the
# move points at the repo root and no longer resolves a SKILL.md; New-Link
# replaces symlinks freely, so re-running this script is the migration.
New-Link (Join-Path $Repo "skills\second-opinion") (Join-Path $ClaudeDir "skills\second-opinion")

# bin/askgpt has a shebang, which Windows does not honour, so the CLI goes on
# PATH as a .cmd that calls the interpreter. `python` is resolved at run time
# rather than baked in, so upgrading Python does not break the install. Its
# presence was already established during preflight.
Set-Content -LiteralPath $Shim -Encoding ascii -Value @"
@echo off
rem ask-gpt shim -- generated by install.ps1; edit the repository instead.
python "$(Join-Path $Repo 'bin\askgpt')" %*
exit /b %ERRORLEVEL%
"@
Write-Host "  wrote   $Shim"

if (($env:PATH -split ";") -notcontains $BinDir) {
    Write-Host "  note: $BinDir is not on your PATH; add it to use ``askgpt`` directly"
}

Write-Host "Verifying Codex..."
# The path travels via the environment rather than being interpolated into a
# Python string literal, which breaks on any repo path containing a backslash
# -- which on Windows is all of them.
$env:ASKGPT_REPO = $Repo
$verify = @'
import os, sys
sys.path.insert(0, os.environ["ASKGPT_REPO"])
from askgpt.codex import find_codex, check_auth, version, version_warning
binary = find_codex()
print("  codex: " + binary)
print("  build: " + (version(binary) or "unknown"))
check_auth(binary)
print("  auth:  ok")
caveat = version_warning(binary)
if caveat:
    print()
    print("  WARNING: " + caveat.replace("\n", "\n           "))
'@
$verify | python -

Write-Host ""
Write-Host "----------------------------------------------------------------------"
Write-Host "Before you use this: Codex's read-only sandbox prevents WRITES, not"
Write-Host "reads. Reads are NOT confined to the repository -- a read-only run was"
Write-Host "measured reading a file in `$HOME. Anything your user account can read"
Write-Host "is reachable, including ~/.ssh and ~/.aws. The preflight scans the"
Write-Host "repository because that is where accidents usually are, not because"
Write-Host "reads stop there. Use a container if that reach is unacceptable."
Write-Host "----------------------------------------------------------------------"
Write-Host ""
Write-Host "Commands: /gptreview  /askgpt  /gptfollow  /gptusage"
Write-Host "Terminal: askgpt usage"
