$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$UvVersion = "0.11.31"
$UvReleaseUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion"
$UvChecksumsSha256 = "cae3a06391dd65895dc22246115fd998250fa43ab3aa8ffd0d6ab71ae301b4e1"
$UvLicenseUrl = "https://raw.githubusercontent.com/astral-sh/uv/b7fdec626cdafcfb0d0db54d39d3d5f114aefb5c/LICENSE-MIT"
$UvLicenseSha256 = "860e3d7a86b84e6a7012c7a635fc64df475cebc6cce34dfeb73a5982ec58176c"
$FiloVersion = "v0.4.0"
$SourceUrl = "https://github.com/matteodante/filo/archive/refs/tags/$FiloVersion.zip"

if ($env:OS -ne "Windows_NT") {
    throw "This installer is for Windows. Use install.sh on macOS or Linux."
}
if (-not [Environment]::UserInteractive) {
    throw "Filo setup requires an interactive terminal."
}

$TemporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) (
    "filo-install-" + [Guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $TemporaryDirectory | Out-Null

try {
    # The install needs uv features newer than some packaged uv releases, so only the pinned
    # version is used. A uv on PATH at another version is left untouched, and the verified
    # download runs from the temporary directory for this installation only.
    $Uv = $null
    $UvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $UvCommand) {
        try {
            $UvFoundVersion = (& $UvCommand.Source --version | Out-String).Trim()
        } catch {
            $UvFoundVersion = ""
        }
        if ($UvFoundVersion -match "^uv $([Regex]::Escape($UvVersion))(\s|$)") {
            $Uv = $UvCommand.Source
        }
    }
    if ($null -eq $Uv) {
        Write-Host "Installing uv $UvVersion..."

        $Architecture = if ($env:PROCESSOR_ARCHITEW6432) {
            $env:PROCESSOR_ARCHITEW6432
        } else {
            $env:PROCESSOR_ARCHITECTURE
        }
        $UvTarget = switch ($Architecture) {
            "AMD64" { "x86_64-pc-windows-msvc" }
            "ARM64" { "aarch64-pc-windows-msvc" }
            "x86" { "i686-pc-windows-msvc" }
            default { throw "uv $UvVersion has no supported artifact for Windows $Architecture." }
        }
        $UvArchiveName = "uv-$UvTarget.zip"
        $UvChecksums = Join-Path $TemporaryDirectory "uv-sha256.sum"
        $UvArchive = Join-Path $TemporaryDirectory $UvArchiveName
        $UvLicense = Join-Path $TemporaryDirectory "uv-LICENSE-MIT"

        Invoke-WebRequest -Uri "$UvReleaseUrl/sha256.sum" -OutFile $UvChecksums
        $UvChecksumsActualSha256 = (Get-FileHash -LiteralPath $UvChecksums -Algorithm SHA256).Hash
        if ($UvChecksumsActualSha256 -ne $UvChecksumsSha256) {
            throw "The downloaded uv checksum manifest failed SHA-256 verification."
        }

        $UvChecksumLine = Get-Content -LiteralPath $UvChecksums |
            Where-Object { $_ -match "\*$([Regex]::Escape($UvArchiveName))$" } |
            Select-Object -First 1
        if ($null -eq $UvChecksumLine) {
            throw "The verified uv checksum manifest has no entry for $UvArchiveName."
        }
        $UvArchiveSha256 = ($UvChecksumLine -split "\s+")[0]

        Invoke-WebRequest -Uri "$UvReleaseUrl/$UvArchiveName" -OutFile $UvArchive
        $UvArchiveActualSha256 = (Get-FileHash -LiteralPath $UvArchive -Algorithm SHA256).Hash
        if ($UvArchiveActualSha256 -ne $UvArchiveSha256) {
            throw "The downloaded uv archive failed SHA-256 verification."
        }
        Invoke-WebRequest -Uri $UvLicenseUrl -OutFile $UvLicense
        $UvLicenseActualSha256 = (Get-FileHash -LiteralPath $UvLicense -Algorithm SHA256).Hash
        if ($UvLicenseActualSha256 -ne $UvLicenseSha256) {
            throw "The downloaded uv license failed SHA-256 verification."
        }

        $UvExtractDirectory = Join-Path $TemporaryDirectory "uv"
        Expand-Archive -LiteralPath $UvArchive -DestinationPath $UvExtractDirectory
        $UvInstallDirectory = if ($null -eq $UvCommand) {
            Join-Path $HOME ".local\bin"
        } else {
            Join-Path $TemporaryDirectory "uv-bin"
        }
        New-Item -ItemType Directory -Path $UvInstallDirectory -Force | Out-Null
        foreach ($UvBinary in @("uv.exe", "uvx.exe", "uvw.exe")) {
            $UvSource = Join-Path $UvExtractDirectory $UvBinary
            if (-not (Test-Path -LiteralPath $UvSource -PathType Leaf)) {
                throw "The verified uv archive is incomplete."
            }
            Copy-Item -LiteralPath $UvSource -Destination $UvInstallDirectory -Force
        }
        Copy-Item -LiteralPath $UvLicense -Destination $UvInstallDirectory -Force

        $Uv = Join-Path $UvInstallDirectory "uv.exe"
        if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
            throw "uv was installed but could not be found at $Uv."
        }
    }

    $Archive = Join-Path $TemporaryDirectory "filo.zip"
    $SourceDirectory = Join-Path $TemporaryDirectory "source"

    Write-Host "Downloading Filo $FiloVersion..."
    Invoke-WebRequest -Uri $SourceUrl -OutFile $Archive
    Expand-Archive -LiteralPath $Archive -DestinationPath $SourceDirectory
    $ProjectDirectory = Get-ChildItem -LiteralPath $SourceDirectory -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "pyproject.toml") } |
        Select-Object -First 1
    if ($null -eq $ProjectDirectory) {
        throw "The downloaded Filo source is incomplete."
    }

    Write-Host "Installing Filo..."
    # uv tool install does not read uv.lock, so the tested versions are passed as constraints.
    # torch stays unpinned outside Linux, where the locked release has no wheel for older Macs.
    $Constraints = Join-Path $TemporaryDirectory "filo-constraints.txt"
    $LockedVersions = & $Uv export --project $ProjectDirectory.FullName --frozen --no-dev `
        --no-emit-project --no-hashes --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not read the Filo lockfile."
    }
    $LockedVersions |
        Where-Object { $_ -notmatch "^torch==.*sys_platform != 'linux'" } |
        Set-Content -LiteralPath $Constraints -Encoding ascii
    & $Uv tool install --force --python 3.12 --constraints $Constraints $ProjectDirectory.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not install Filo."
    }

    $ToolBin = (& $Uv tool dir --bin | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not locate its tool directory."
    }
    # Filo is already installed when the PATH update runs, so a failure there does not stop setup;
    # the user is told which directory to add.
    & $Uv tool update-shell
    $PathUpdated = $LASTEXITCODE -eq 0
    if (-not $PathUpdated) {
        Write-Warning (
            "uv could not add $ToolBin to PATH. " +
            "Add it yourself to run filo from a new PowerShell."
        )
    }

    $Filo = Join-Path $ToolBin "filo.exe"
    if (-not (Test-Path -LiteralPath $Filo -PathType Leaf)) {
        throw "Filo was installed but $Filo is unavailable."
    }

    & $Filo setup
    if ($LASTEXITCODE -ne 0) {
        throw "Filo setup did not complete."
    }
    & $Filo doctor
    if ($LASTEXITCODE -ne 0) {
        throw "Filo doctor reported an error."
    }

    Write-Host ""
    Write-Host "Filo is ready. Start it with: filo chat"
    if ($PathUpdated) {
        Write-Host "If filo is not found in a new command, restart PowerShell."
    } else {
        Write-Host "filo is not on PATH yet: add $ToolBin to PATH first."
    }
} finally {
    Remove-Item -LiteralPath $TemporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
