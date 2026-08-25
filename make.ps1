<#
.SYNOPSIS
    The Makefile targets, for Windows where `make` is usually absent.

.DESCRIPTION
    Mirrors Makefile exactly. Two differences are forced by the platform and
    are not choices:

      * the interpreter is `python`, because a default Windows install of
        Python provides no `python3` executable at all;
      * `acceptance` runs the PowerShell end-to-end script, since the bash one
        installs via install.sh and symlinks the way a Unix host does.

.EXAMPLE
    .\make.ps1 test
    .\make.ps1 lint
    .\make.ps1 acceptance
#>

param(
    [ValidateSet("test", "lint", "acceptance")]
    [string]$Target = "test"
)

$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot

Push-Location $Repo
try {
    switch ($Target) {
        "test" {
            python -m unittest discover -s tests -v
            # `Ran 279 tests` is not a pass. The exit code is, so surface it.
            #
            # Do NOT pipe this through `2>&1` when invoking it. unittest writes
            # its report to stderr, and Windows PowerShell 5.1 wraps a native
            # command's stderr in NativeCommandError records and sets $? to
            # false -- a fully passing run then looks like a failure. Observed
            # while building this: `.\make.ps1 test 2>&1 | Select-Object` showed
            # exit 1 on a run that genuinely exited 0.
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        "lint" {
            python -m compileall -q askgpt bin/askgpt
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        "acceptance" {
            $script = Join-Path $Repo "tests\acceptance.ps1"
            if (-not (Test-Path $script)) {
                Write-Error "tests\acceptance.ps1 is missing; the bash suite in tests/acceptance.sh installs the Unix way and does not apply here."
            }
            & powershell -NoProfile -ExecutionPolicy Bypass -File $script
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
    }
} finally {
    Pop-Location
}
