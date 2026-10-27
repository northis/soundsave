<#
.SYNOPSIS
    Update Soundsave from the console: git pull + docker build + container restart.
.DESCRIPTION
    Host-side update without a daemon. The script fast-forwards the working copy to
    origin/<branch>, builds the image from a clean worktree of the target commit (uncommitted
    edits in the working copy never reach the image), recreates the container and waits until
    it reports the new commit. If the new container does not come up, the previous image
    (soundsave:previous) is restored. Only git and the docker CLI run; the working copy is
    never reset, cleaned or checked out.
.PARAMETER Branch
    Branch to update from. Default: master.
.PARAMETER Force
    Rebuild and restart even when the container already runs the target commit.
.PARAMETER SkipSnapshot
    Do not take a pre-update database snapshot inside the running container.
.PARAMETER KeepImages
    How many old soundsave:<sha> images to keep. Default: 3.
.PARAMETER Rollback
    Do not update: restore soundsave:previous and restart the container with it.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\windows\update.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\windows\update.ps1 -Rollback
.NOTES
    Keep this file ASCII-only. Windows PowerShell 5.1 decodes a BOM-less script with the
    system codepage, so a UTF-8 em dash becomes several characters whose last one is a smart
    quote; PowerShell accepts smart quotes as string delimiters, the string ends early and
    the rest of the line is parsed - and run - as commands.
#>
[CmdletBinding()]
param(
    [string]$Branch = "master",
    [switch]$Force,
    [switch]$SkipSnapshot,
    [int]$KeepImages = 3,
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Image = "soundsave"
$Container = "soundsave"
$HealthTimeout = 180
$HealthInterval = 3

function Write-Step {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# Run a native command. Without -Capture the output streams to the console and the function
# returns $true/$false; with -Capture it returns stdout (trimmed) or $null on failure.
# -AllowFailure turns a non-zero exit code into $false/$null instead of an exception.
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$Command,
        [string[]]$Arguments = @(),
        [switch]$Capture,
        [switch]$AllowFailure
    )
    $line = ("{0} {1}" -f $Command, ($Arguments -join " ")).Trim()
    if (-not $Capture) { Write-Host "    $line" -ForegroundColor DarkGray }
    $savedEap = $ErrorActionPreference
    $savedNative = $null
    if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
        $savedNative = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    $ErrorActionPreference = "Continue"
    try {
        if ($Capture) { $output = & $Command @Arguments 2>&1 } else { & $Command @Arguments }
    } finally {
        $ErrorActionPreference = $savedEap
        if ($null -ne $savedNative) { $PSNativeCommandUseErrorActionPreference = $savedNative }
    }
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        if (-not $AllowFailure) { throw "'$line' exited with code $code" }
        if ($Capture) { return $null }
        return $false
    }
    if ($Capture) { return ($output | Out-String).Trim() }
    return $true
}

function Get-Version {
    param([Parameter(Mandatory)][string]$Commit)
    $source = Invoke-Native -Command "git" -Arguments @("show", "${Commit}:src/soundsave/__init__.py") -Capture
    $match = [regex]::Match($source, "__version__\s*=\s*'([^']+)'")
    if (-not $match.Success) { throw "cannot read __version__ from commit $Commit" }
    return $match.Groups[1].Value
}

# The commit the running container reports through /api/version: $null when the container
# does not answer (not running / still starting), "" when it answers without a commit.
function Get-ContainerCommit {
    $probe = "import json,urllib.request;" +
             "print(json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/version',timeout=3))['commit'] or '')"
    $output = Invoke-Native -Command "docker" -Arguments @("exec", $Container, "python", "-c", $probe) `
        -Capture -AllowFailure
    if ($null -eq $output) { return $null }
    return $output.Trim()
}

function Wait-ContainerReady {
    param([string]$Expected = "")
    $deadline = (Get-Date).AddSeconds($HealthTimeout)
    while ((Get-Date) -lt $deadline) {
        $commit = Get-ContainerCommit
        if ($null -ne $commit) {
            if (-not $Expected) { return $true }
            $want = $Expected.Substring(0, [Math]::Min(12, $Expected.Length))
            $got = $commit.Substring(0, [Math]::Min(12, $commit.Length))
            if ($got -eq $want) { return $true }
        }
        Start-Sleep -Seconds $HealthInterval
    }
    return $false
}

function Get-ImageCommit {
    param([Parameter(Mandatory)][string]$Tag)
    $format = '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
    $revision = Invoke-Native -Command "docker" -Arguments @("inspect", "--format", $format, $Tag) `
        -Capture -AllowFailure
    if (-not $revision -or $revision -eq "<no value>") { return "" }
    return $revision.Trim()
}

function Restore-PreviousImage {
    param([Parameter(Mandatory)][string]$Reason)
    Write-Warning "rollback: $Reason"
    if (-not (Invoke-Native -Command "docker" -Arguments @("image", "inspect", "${Image}:previous") `
            -Capture -AllowFailure)) {
        throw "cannot roll back: the ${Image}:previous image does not exist"
    }
    $previous = Get-ImageCommit -Tag "${Image}:previous"
    Invoke-Native -Command "docker" -Arguments @("image", "tag", "${Image}:previous", "${Image}:latest")
    Invoke-Native -Command "docker" -Arguments @("compose", "--project-directory", $Repo, `
        "up", "-d", "--no-build", $Container)
    if (-not (Wait-ContainerReady -Expected $previous)) {
        throw "rollback failed: the container did not become healthy"
    }
    Write-Host "Rolled back to ${Image}:previous ($previous)." -ForegroundColor Yellow
}

function Remove-OldImages {
    param([Parameter(Mandatory)][int]$Keep)
    $listing = Invoke-Native -Command "docker" -Arguments @("image", "ls", $Image, "--format", `
        "{{.Tag}}|{{.CreatedAt}}") -Capture -AllowFailure
    if (-not $listing) { return }
    $rows = @()
    foreach ($line in $listing.Split("`n")) {
        $parts = $line.Trim() -split "\|", 2
        if ($parts.Count -eq 2 -and $parts[0] -match "^[0-9a-f]{12}$") {
            $rows += [pscustomobject]@{ Tag = $parts[0]; Created = $parts[1] }
        }
    }
    $stale = @($rows | Sort-Object Created -Descending | Select-Object -Skip $Keep)
    foreach ($row in $stale) {
        Invoke-Native -Command "docker" -Arguments @("image", "rm", "${Image}:$($row.Tag)") `
            -AllowFailure | Out-Null
    }
    if ($stale.Count -gt 0) { Write-Host "Removed $($stale.Count) old image(s); kept the $Keep newest." }
}

Write-Step "Soundsave update in $Repo (branch $Branch)"
Push-Location $Repo
try {
    foreach ($tool in @("git", "docker")) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            throw "$tool was not found in PATH"
        }
    }
    Write-Step "Checking Docker"
    Invoke-Native -Command "docker" -Arguments @("info") -Capture | Out-Null

    if ($Rollback) {
        Restore-PreviousImage -Reason "-Rollback was requested"
        exit 0
    }

    Write-Step "Fetching origin/$Branch"
    # --tags: a fetch with an explicit refspec does not follow tags, and the release tag
    # must land in the working copy so the version check below can see it.
    Invoke-Native -Command "git" -Arguments @("fetch", "--prune", "--tags", "origin", $Branch)

    $current = Invoke-Native -Command "git" -Arguments @("symbolic-ref", "--short", "HEAD") -Capture
    if ($current -ne $Branch) {
        throw "the working copy is on '$current', not '$Branch' (switch branches or pass -Branch)"
    }

    Write-Step "Fast-forwarding the working copy"
    Invoke-Native -Command "git" -Arguments @("merge", "--ff-only", "origin/$Branch")

    $target = Invoke-Native -Command "git" -Arguments @("rev-parse", "HEAD") -Capture
    $remote = Invoke-Native -Command "git" -Arguments @("rev-parse", "origin/$Branch") -Capture
    if ($target -ne $remote) {
        throw "HEAD $target is not origin/$Branch $remote (unpushed commits?)"
    }
    $short = $target.Substring(0, 12)
    $version = Get-Version -Commit $target
    $tags = @((Invoke-Native -Command "git" -Arguments @("tag", "--points-at", $target) -Capture) `
            -split "`n" | ForEach-Object { $_.Trim() })
    if ($tags -notcontains "v$version") {
        Write-Warning "commit $short is not tagged v$version - create and push the release tag first"
    }

    Write-Step "Checking the running container"
    $deployed = Get-ContainerCommit
    if (-not $Force -and $deployed -and $deployed.StartsWith($short)) {
        Write-Host "Already running $short (v$version) - nothing to do. Use -Force to rebuild." -ForegroundColor Green
        exit 0
    }

    if (-not $SkipSnapshot) {
        Write-Step "Snapshotting the database (pre-update)"
        $ok = Invoke-Native -Command "docker" -Arguments @("exec", $Container, "python", "-m", "soundsave", `
                "snapshot", "--label", "pre-update") -AllowFailure
        if (-not $ok) { Write-Warning "the snapshot failed; continuing" }
    }

    Write-Step "Building the image from a clean worktree ($short)"
    if (Invoke-Native -Command "docker" -Arguments @("image", "inspect", "${Image}:latest") `
            -Capture -AllowFailure) {
        Invoke-Native -Command "docker" -Arguments @("image", "tag", "${Image}:latest", "${Image}:previous")
    }
    $worktree = Join-Path ([System.IO.Path]::GetTempPath()) "soundsave-build-$short"
    if (Test-Path $worktree) { Remove-Item -LiteralPath $worktree -Recurse -Force }
    Invoke-Native -Command "git" -Arguments @("worktree", "prune")
    Invoke-Native -Command "git" -Arguments @("worktree", "add", "--detach", $worktree, $target)
    try {
        $commitDate = Invoke-Native -Command "git" -Arguments @("show", "-s", "--format=%cI", $target) -Capture
        $created = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        Invoke-Native -Command "docker" -Arguments @(
            "build", "-t", "${Image}:latest", "-t", "${Image}:$short",
            "--build-arg", "SOUNDSAVE_COMMIT=$target",
            "--build-arg", "SOUNDSAVE_COMMIT_DATE=$commitDate",
            "--build-arg", "SOUNDSAVE_VERSION=$version",
            "--build-arg", "SOUNDSAVE_CREATED=$created",
            $worktree
        )
    } finally {
        Invoke-Native -Command "git" -Arguments @("worktree", "remove", "--force", $worktree) `
            -AllowFailure | Out-Null
        Invoke-Native -Command "git" -Arguments @("worktree", "prune") -AllowFailure | Out-Null
    }

    Write-Step "Restarting the container"
    try {
        Invoke-Native -Command "docker" -Arguments @("compose", "--project-directory", $Repo, `
            "up", "-d", "--no-build", $Container)
        if (-not (Wait-ContainerReady -Expected $target)) {
            throw "the new container did not report commit $short within $HealthTimeout s"
        }
    } catch {
        Restore-PreviousImage -Reason $_
        exit 1
    }

    Remove-OldImages -Keep $KeepImages
    Write-Step "Done: v$version ($short) is running"
} finally {
    Pop-Location
}
