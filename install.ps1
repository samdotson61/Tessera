# Tessera installer (Windows).
#   powershell -c "irm https://raw.githubusercontent.com/samdotson61/Tessera/main/install.ps1 | iex"
#
# Downloads the prebuilt tessera.exe from the latest GitHub release into
# %LOCALAPPDATA%\Tessera and adds it to the user PATH.
$ErrorActionPreference = "Stop"

$repo = "samdotson61/Tessera"
$dir = Join-Path $env:LOCALAPPDATA "Tessera"
$exe = Join-Path $dir "tessera.exe"
$url = "https://github.com/$repo/releases/latest/download/tessera-windows-x64.exe"

New-Item -ItemType Directory -Force -Path $dir | Out-Null
Write-Host "tessera: downloading the prebuilt binary from the latest release..."
Invoke-WebRequest -Uri $url -OutFile $exe

& $exe --help | Out-Null
Write-Host "tessera: installed to $exe"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$dir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$dir", "User")
    Write-Host "tessera: added $dir to your user PATH - open a NEW terminal for it to take effect."
}

Write-Host ""
Write-Host "next:  tessera app     (opens the review UI; first run loads an offline sample)"
