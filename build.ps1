[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildPath = Join-Path $projectRoot "build"
$distPath = Join-Path $projectRoot "dist"
Push-Location $projectRoot

try {
    & python --version
    if ($LASTEXITCODE -ne 0) {
        throw "Python was not found on PATH."
    }

    $pythonBits = (& python -c "import struct; print(struct.calcsize('P') * 8)").Trim()
    if ($LASTEXITCODE -ne 0 -or $pythonBits -ne "64") {
        throw "A 64-bit Python installation is required to build the Windows x64 executable."
    }

    if (-not $SkipTests) {
        & python -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed; the executable was not built."
        }
    }

    & python -c "import PyInstaller"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is missing. Run: python -m pip install -r requirements-build.txt"
    }

    if (-not (Test-Path -LiteralPath $buildPath -PathType Container)) {
        New-Item -ItemType Directory -Path $buildPath | Out-Null
    }

    $versionInfoPath = Join-Path $buildPath "version_info.txt"
    & python (Join-Path $projectRoot "build_version_info.py") --output $versionInfoPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $versionInfoPath -PathType Leaf)) {
        throw "Could not generate the Windows version resource."
    }

    & python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name "RTZ-to-CSV" `
        --version-file $versionInfoPath `
        --distpath $distPath `
        --workpath $buildPath `
        --specpath $buildPath `
        (Join-Path $projectRoot "rtz_to_csv.py")

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }

    $executable = Join-Path $distPath "RTZ-to-CSV.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Build completed without producing $executable"
    }

    $version = (& python -c "from app_metadata import APP_VERSION; print(APP_VERSION)").Trim()
    $fileVersion = (Get-Item -LiteralPath $executable).VersionInfo.FileVersion
    $productVersion = (Get-Item -LiteralPath $executable).VersionInfo.ProductVersion
    if ($fileVersion -notlike "$version*" -or $productVersion -notlike "$version*") {
        throw "Executable version metadata does not match APP_VERSION $version."
    }

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $executable).Hash.ToLowerInvariant()
    $checksumPath = "$executable.sha256"
    "$hash  RTZ-to-CSV.exe" | Set-Content -LiteralPath $checksumPath -Encoding ascii

    Write-Host "Built successfully: $executable"
    Write-Host "SHA-256 manifest: $checksumPath"
}
finally {
    Pop-Location
}
