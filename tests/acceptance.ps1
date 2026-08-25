<#
    End-to-end acceptance against a fresh install, using a stub Codex.
    The Windows counterpart to acceptance.sh, checking the same claims.

    This layer exists because it is the one that found the bugs that mattered:
    auth rejecting real users, quota detection discarding valid reviews, the
    follow path being unreachable, documented commands that could not be run.
    None of those were caught by unit tests.

    Streams are captured SEPARATELY throughout, via cmd redirection rather than
    PowerShell's `2>`. Two reasons, both learned the hard way: merging them with
    2>&1 is exactly what hid the auth bug, since `codex login status` writes to
    stderr; and PowerShell 5.1 wraps a native command's stderr in ErrorRecords
    and sets $? to false even on a clean exit, so it cannot be trusted to say
    what a process actually wrote where.
#>

$ErrorActionPreference = "Continue"

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Work = Join-Path ([IO.Path]::GetTempPath()) ("askgpt-accept-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$script:Pass = 0
$script:Fail = 0

function ok($name) { $script:Pass++; Write-Host "  PASS  $name" }
function no($name, $detail) {
    $script:Fail++
    Write-Host "  FAIL  $name"
    if ($detail) { Write-Host "        $detail" }
}
function check($condition, $name, $detail) {
    if ($condition) { ok $name } else { no $name $detail }
}

function Invoke-Captured {
    <# Run a command, keeping stdout and stderr in separate files. #>
    param([string[]]$Command, [string]$OutFile, [string]$ErrFile)
    $quoted = ($Command | ForEach-Object { '"' + $_ + '"' }) -join " "
    cmd /c "$quoted > `"$OutFile`" 2> `"$ErrFile`"" | Out-Null
    return $LASTEXITCODE
}

function FileHas($path, $pattern) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    return [bool](Select-String -LiteralPath $path -Pattern $pattern -Quiet -ErrorAction SilentlyContinue)
}

function IsSymlink($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    return [bool]((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)
}

try {
    # A stub Codex: answers `login status` on STDERR, exactly as the real CLI
    # does. Written as .py plus a .cmd launcher because a shebang is not
    # executable on Windows -- the same reason tests/stubs.py exists.
    $stubPy = Join-Path $Work "codex.py"
    $stubCmd = Join-Path $Work "codex.cmd"
    $argvLog = Join-Path $Work "codex.argv"
    $modeFile = Join-Path $Work "codex.mode"

    @"
import sys, pathlib
argv = sys.argv
pathlib.Path(r'$argvLog').write_text(' '.join(argv))
if 'login' in argv:
    sys.stderr.write('Logged in using ChatGPT\n')   # stderr, like the real one
    sys.exit(0)
mode = pathlib.Path(r'$modeFile')
if mode.exists() and mode.read_text().strip() == 'fail':
    print('{"type":"error","message":"connection reset"}')
    sys.exit(1)
out = argv[argv.index('-o') + 1]
pathlib.Path(out).write_text('STUB REVIEW BODY')
print('{"type":"thread.started","thread_id":"ACCEPT1"}')
"@ | Set-Content -LiteralPath $stubPy -Encoding ascii

    Set-Content -LiteralPath $stubCmd -Encoding ascii -Value @"
@echo off
python "$stubPy" %*
exit /b %ERRORLEVEL%
"@

    $env:CODEX_BIN = $stubCmd
    $env:CLAUDE_CONFIG_DIR = Join-Path $Work "claude"
    $env:ASKGPT_BIN_DIR = Join-Path $Work "bin"
    $env:ASKGPT_STATE_DIR = Join-Path $Work "state"
    New-Item -ItemType Directory -Force -Path $env:CLAUDE_CONFIG_DIR | Out-Null

    Write-Host "1. install from a fresh checkout"
    $fresh = Join-Path $Work "fresh"
    git clone -q $Repo $fresh 2>&1 | Out-Null
    $rc = Invoke-Captured @("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $fresh "install.ps1")) `
        (Join-Path $Work "i.out") (Join-Path $Work "i.err")
    check ($rc -eq 0) "installer exits 0" (Get-Content (Join-Path $Work "i.err") -TotalCount 3 -ErrorAction SilentlyContinue)
    check (FileHas (Join-Path $Work "i.out") "auth:  ok") "installer verifies auth"
    foreach ($c in @("gptreview", "askgpt", "gptfollow", "gptusage")) {
        check (IsSymlink (Join-Path $env:CLAUDE_CONFIG_DIR "commands\$c.md")) "/$c installed"
    }
    $cli = Join-Path $env:ASKGPT_BIN_DIR "askgpt.cmd"
    check (Test-Path -LiteralPath $cli) "askgpt is on PATH"

    Write-Host "2. run the primary command from ANOTHER repository"
    $other = Join-Path $Work "other"
    New-Item -ItemType Directory -Force -Path $other | Out-Null
    git init -q -b main $other
    git -C $other config user.email a@b.c
    git -C $other config user.name T
    "hello" | Set-Content (Join-Path $other "f.txt")
    git -C $other add -A
    git -C $other commit -qm init
    "changed" | Set-Content (Join-Path $other "f.txt")

    $rc = Invoke-Captured @($cli, "review", "--uncommitted", "--task", "acceptance", "--session-id", "S1", "--cwd", $other) `
        (Join-Path $Work "r.out") (Join-Path $Work "r.err")
    check ($rc -eq 0) "review exits 0 from another repo" (Get-Content (Join-Path $Work "r.err") -TotalCount 3 -ErrorAction SilentlyContinue)
    check (FileHas (Join-Path $Work "r.out") "STUB REVIEW BODY") "review body on STDOUT"
    check (FileHas (Join-Path $Work "r.err") "Target:") "progress on STDERR, not stdout"
    check (FileHas $argvLog "read-only") "codex invoked read-only"
    check (FileHas $argvLog "gpt-5.6-sol") "pinned model requested"

    Write-Host "3. exercise a failure response"
    "fail" | Set-Content -LiteralPath $modeFile
    $rc = Invoke-Captured @($cli, "review", "--uncommitted", "--task", "acceptance", "--cwd", $other) `
        (Join-Path $Work "f.out") (Join-Path $Work "f.err")
    check ($rc -ne 0) "failure exits non-zero"
    check (FileHas (Join-Path $Work "f.err") "connection reset") "real cause surfaced"
    $failOut = Get-Item -LiteralPath (Join-Path $Work "f.out") -ErrorAction SilentlyContinue
    check ($null -eq $failOut -or $failOut.Length -eq 0) "no review printed on failure"
    Remove-Item -LiteralPath $modeFile -Force -ErrorAction SilentlyContinue

    Write-Host "4. continue the resulting thread"
    $threads = Join-Path $env:ASKGPT_STATE_DIR "threads"
    $persisted = $false
    if (Test-Path -LiteralPath $threads) {
        $persisted = [bool](Get-ChildItem -LiteralPath $threads -File -ErrorAction SilentlyContinue |
            Where-Object { (Get-Content -LiteralPath $_.FullName -Raw) -match "ACCEPT1" })
    }
    check $persisted "thread id persisted"
    $rc = Invoke-Captured @($cli, "follow", "and another thing", "--session-id", "S1", "--cwd", $other) `
        (Join-Path $Work "fo.out") (Join-Path $Work "fo.err")
    check ($rc -eq 0) "follow exits 0" (Get-Content (Join-Path $Work "fo.err") -TotalCount 3 -ErrorAction SilentlyContinue)
    check (FileHas $argvLog "resume ACCEPT1") "resumed by exact id"
    check (-not (FileHas $argvLog "\-\-last")) "never used --last"

    Write-Host "5. follow the README from scratch"
    check (FileHas (Join-Path $Work "i.out") "not confined to the repository|NOT confined to the repository") `
        "installer discloses the read boundary"
    check (FileHas (Join-Path $Repo "README.md") "optional") "README declares superpowers optional"
    check (FileHas (Join-Path $Repo "commands\gptreview.md") "If the .superpowers:receiving-code-review. skill is available") `
        "gptreview does not hard-depend on superpowers"
    foreach ($cmd in @("/gptreview", "/askgpt", "/gptfollow", "/gptusage")) {
        check (FileHas (Join-Path $Repo "README.md") ([regex]::Escape($cmd))) "README documents $cmd"
    }
    $rc = Invoke-Captured @($cli, "review", "--help") (Join-Path $Work "h.out") (Join-Path $Work "h.err")
    foreach ($flag in @("--dry-run", "--keep", "--no-fallback", "--allow-secrets", "--allow-sensitive-files")) {
        check (FileHas (Join-Path $Work "h.out") ([regex]::Escape($flag))) "README flag $flag exists in the CLI"
    }
    $rc = Invoke-Captured @($cli, "usage") (Join-Path $Work "u.out") (Join-Path $Work "u.err")
    check ($rc -eq 0) "usage runs" (Get-Content (Join-Path $Work "u.err") -TotalCount 3 -ErrorAction SilentlyContinue)

    Write-Host "6. no overstated claims in user-facing text"
    # Every one of these was true of an earlier design and became false when the
    # design changed. Documentation drift is the failure mode this project hits
    # most often, so the retired phrasings are asserted absent rather than
    # trusted to stay gone.
    $stale = "quota is left|remaining plan quota|exactly what would leave|nothing is modified on disk|no other dependencies|fails closed rather than"
    $targets = @((Join-Path $Repo "README.md"), (Join-Path $Repo "SKILL.md")) +
               (Get-ChildItem -LiteralPath (Join-Path $Repo "commands") -File | ForEach-Object { $_.FullName })
    $hits = Select-String -LiteralPath $targets -Pattern $stale -ErrorAction SilentlyContinue
    if ($hits) {
        no "user-facing text free of retired claims" (($hits | Select-Object -First 3 | ForEach-Object { $_.Line }) -join "; ")
    } else {
        ok "user-facing text free of retired claims"
    }
    # The strong claim must never appear without its qualifier nearby.
    $unqualified = Select-String -LiteralPath (Join-Path $Repo "README.md") -Pattern "independent review" -ErrorAction SilentlyContinue |
        Where-Object { $_.Line -notmatch "not an independent|transcript-blind" }
    if ($unqualified) {
        no "no unqualified 'independent review' claim" $unqualified[0].Line
    } else {
        ok "no unqualified 'independent review' claim"
    }

    Write-Host ""
    Write-Host "acceptance: $script:Pass passed, $script:Fail failed"
    if ($script:Fail -ne 0) { exit 1 }
    exit 0
} finally {
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
}
