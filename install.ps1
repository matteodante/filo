$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$UvVersion = "0.11.31"
$UvReleaseUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion"
$UvChecksumsSha256 = "cae3a06391dd65895dc22246115fd998250fa43ab3aa8ffd0d6ab71ae301b4e1"
$UvLicenseUrl = "https://raw.githubusercontent.com/astral-sh/uv/b7fdec626cdafcfb0d0db54d39d3d5f114aefb5c/LICENSE-MIT"
$UvLicenseSha256 = "860e3d7a86b84e6a7012c7a635fc64df475cebc6cce34dfeb73a5982ec58176c"
$FiloVersion = "v0.3.1"
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
    $UvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $UvCommand) {
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
        $UvInstallDirectory = Join-Path $HOME ".local\bin"
        New-Item -ItemType Directory -Path $UvInstallDirectory -Force | Out-Null
        foreach ($UvBinary in @("uv.exe", "uvx.exe", "uvw.exe")) {
            $UvSource = Join-Path $UvExtractDirectory $UvBinary
            if (-not (Test-Path -LiteralPath $UvSource -PathType Leaf)) {
                throw "The verified uv archive is incomplete."
            }
            Copy-Item -LiteralPath $UvSource -Destination $UvInstallDirectory -Force
        }
        Copy-Item -LiteralPath $UvLicense -Destination $UvInstallDirectory -Force

        $Uv = Join-Path $HOME ".local\bin\uv.exe"
        if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
            throw "uv was installed but could not be found at $Uv."
        }
    } else {
        $Uv = $UvCommand.Source
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
    & $Uv tool install --force --python 3.12 $ProjectDirectory.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not install Filo."
    }
    & $Uv tool update-shell
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not add its tool directory to PATH."
    }

    $ToolBin = (& $Uv tool dir --bin | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not locate its tool directory."
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
    Write-Host "If filo is not found in a new command, restart PowerShell."
} finally {
    Remove-Item -LiteralPath $TemporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
