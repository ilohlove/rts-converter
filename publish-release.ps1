[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    $branch = (git branch --show-current).Trim()
    if ($branch -ne "main") {
        throw "Release publishing must start from the main branch (current: $branch)."
    }
    if ((git status --porcelain)) {
        throw "Commit all source changes before publishing a release."
    }
    $version = (python -c "from app_metadata import APP_VERSION; print(APP_VERSION)").Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw "Could not read a valid APP_VERSION."
    }
    $tag = "v$version"
    if (git tag --list $tag) {
        throw "Tag $tag already exists. Bump APP_VERSION before publishing."
    }
    if (-not $SkipTests) {
        python -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
    }
    git push origin main
    git tag -a $tag -m "Release $tag"
    git push origin $tag
    Write-Host "Published $tag. GitHub Actions will build and publish the immutable release."
}
finally {
    Pop-Location
}
