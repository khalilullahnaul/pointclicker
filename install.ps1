# Install PointClicker on Windows: the latest installer from GitHub Releases, checked against SHA256SUMS.txt,
# installed for your user only (no administrator rights), then started.
#
#   irm https://raw.githubusercontent.com/khalilullahnaul/pointclicker/main/install.ps1 | iex
#
# Uninstall from Settings > Apps > PointClicker.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Windows PowerShell 5 downloads are very slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$repo = 'khalilullahnaul/pointclicker'
$headers = @{ 'User-Agent' = 'pointclicker-install' }
try {
    $release = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers $headers
} catch {
    throw "Couldn't reach GitHub (offline, or no release published yet): $_"
}
$setup = $release.assets | Where-Object { $_.name -like 'PointClicker-Setup-*.exe' } | Select-Object -First 1
$sums = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS.txt' } | Select-Object -First 1
if (-not $setup -or -not $sums) { throw "The latest release ($($release.tag_name)) has no installer or SHA256SUMS.txt." }

$dir = Join-Path ([IO.Path]::GetTempPath()) "pointclicker-install-$PID"
New-Item -ItemType Directory -Force $dir | Out-Null
try {
    $exe = Join-Path $dir $setup.name
    Write-Host "Downloading $($setup.name)..."
    Invoke-WebRequest $setup.browser_download_url -OutFile $exe -Headers $headers -UseBasicParsing
    Invoke-WebRequest $sums.browser_download_url -OutFile "$dir\SHA256SUMS.txt" -Headers $headers -UseBasicParsing

    $line = Get-Content "$dir\SHA256SUMS.txt" | Where-Object { ($_ -split '\s+', 2)[1] -replace '^\*', '' -eq $setup.name }
    if (-not $line) { throw "$($setup.name) is not listed in SHA256SUMS.txt" }
    $expected = ($line -split '\s+')[0]
    $actual = (Get-FileHash $exe -Algorithm SHA256).Hash
    if ($actual -ne $expected.ToUpperInvariant()) {
        throw "Checksum mismatch for $($setup.name): the download is corrupt or was tampered with."
    }
    Write-Host 'Checksum OK. Installing (per user, no admin needed)...'

    $p = Start-Process $exe -ArgumentList '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-' -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "The installer failed with exit code $($p.ExitCode)." }
} finally {
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
}

$app = Join-Path $env:LOCALAPPDATA 'Programs\PointClicker\PointClicker.exe'
if (-not (Test-Path $app)) { throw "Installation finished, but $app is missing." }
Write-Host "PointClicker $($release.tag_name) is installed: it's in the Start menu. Starting it now."
if (-not $env:POINTCLICKER_NO_LAUNCH) { Start-Process $app }
