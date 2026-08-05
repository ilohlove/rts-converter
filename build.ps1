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

function Remove-GeneratedArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,
        [Parameter(Mandatory = $true)]
        [string]$AllowedRoot
    )

    $rootPath = [System.IO.Path]::GetFullPath($AllowedRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $artifactPath = [System.IO.Path]::GetFullPath($LiteralPath)
    $requiredPrefix = $rootPath + [System.IO.Path]::DirectorySeparatorChar
    if (-not $artifactPath.StartsWith($requiredPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove generated artifact outside $rootPath`: $artifactPath"
    }

    if (Test-Path -LiteralPath $artifactPath) {
        Remove-Item -LiteralPath $artifactPath -Recurse -Force
    }
}

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

    & python -c "import PIL"
    if ($LASTEXITCODE -ne 0) {
        throw "Pillow is missing. Run: python -m pip install -r requirements-build.txt"
    }

    if (-not (Test-Path -LiteralPath $buildPath -PathType Container)) {
        New-Item -ItemType Directory -Path $buildPath | Out-Null
    }

    if (-not (Test-Path -LiteralPath $distPath -PathType Container)) {
        New-Item -ItemType Directory -Path $distPath | Out-Null
    }

    $iconGenerator = Join-Path $projectRoot "generate_icon.py"
    & python $iconGenerator --check
    if ($LASTEXITCODE -ne 0) {
        throw "Application icon verification failed. Run: python generate_icon.py"
    }

    $iconPngPath = Join-Path $projectRoot "assets\rts_converter.png"
    $iconIcoPath = Join-Path $projectRoot "assets\rts_converter.ico"
    if (
        -not (Test-Path -LiteralPath $iconPngPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $iconIcoPath -PathType Leaf)
    ) {
        throw "The generated PNG or ICO asset is missing."
    }

    $appName = (& python -c "from app_metadata import APP_NAME; print(APP_NAME)").Trim()
    $executableName = (& python -c "from app_metadata import EXECUTABLE_NAME; print(EXECUTABLE_NAME)").Trim()
    if (
        [string]::IsNullOrWhiteSpace($appName) -or
        [string]::IsNullOrWhiteSpace($executableName) -or
        [System.IO.Path]::GetFileName($executableName) -ne $executableName -or
        [System.IO.Path]::GetExtension($executableName) -ne ".exe"
    ) {
        throw "APP_NAME or EXECUTABLE_NAME in app_metadata.py is invalid."
    }
    $executableBaseName = [System.IO.Path]::GetFileNameWithoutExtension($executableName)

    $entrypoint = Join-Path $projectRoot "rts_converter_app.py"
    if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
        throw "GUI entrypoint not found: $entrypoint"
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
        --name $executableBaseName `
        --icon $iconIcoPath `
        --add-data "${iconPngPath}:assets" `
        --add-data "${iconIcoPath}:assets" `
        --version-file $versionInfoPath `
        --distpath $distPath `
        --workpath $buildPath `
        --specpath $buildPath `
        $entrypoint

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }

    $executable = Join-Path $distPath $executableName
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
    "$hash  $executableName" | Set-Content -LiteralPath $checksumPath -Encoding ascii

    $legacyArtifacts = @(
        (Join-Path $distPath "RTZ-to-CSV.exe"),
        (Join-Path $distPath "RTZ-to-CSV.exe.sha256"),
        (Join-Path $buildPath "RTZ-to-CSV"),
        (Join-Path $buildPath "RTZ-to-CSV.spec")
    )
    $currentArtifacts = @(
        [System.IO.Path]::GetFullPath($executable),
        [System.IO.Path]::GetFullPath($checksumPath)
    )
    foreach ($legacyArtifact in $legacyArtifacts) {
        if ($currentArtifacts -notcontains [System.IO.Path]::GetFullPath($legacyArtifact)) {
            Remove-GeneratedArtifact -LiteralPath $legacyArtifact -AllowedRoot $projectRoot
        }
    }

    Write-Host "Built successfully: $executable"
    Write-Host "SHA-256 manifest: $checksumPath"
}
finally {
    Pop-Location
}
