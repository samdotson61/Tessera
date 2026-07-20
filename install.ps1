# Tessera installer (Windows).
#   powershell -c "irm https://raw.githubusercontent.com/samdotson61/Tessera/main/install.ps1 | iex"
#
# Downloads the prebuilt tessera.exe from the latest GitHub release into
# %LOCALAPPDATA%\Tessera (override with $env:TESSERA_DIR) and adds it to the
# user PATH. Idempotent: re-running upgrades in place, even while tessera is
# running (a running exe can be renamed but not overwritten on Windows).
$ErrorActionPreference = "Stop"

$repo = "samdotson61/Tessera"
$dir = if ($env:TESSERA_DIR) { $env:TESSERA_DIR } else { Join-Path $env:LOCALAPPDATA "Tessera" }
$exe = Join-Path $dir "tessera.exe"
$url = "https://github.com/$repo/releases/latest/download/tessera-windows-x64.exe"

New-Item -ItemType Directory -Force -Path $dir | Out-Null
Write-Host "tessera: downloading the prebuilt binary from the latest release..."
$tmp = Join-Path $dir "tessera.download.tmp"
Invoke-WebRequest -Uri $url -OutFile $tmp

# stale .old from a previous upgrade-while-running: best-effort cleanup
Remove-Item "$exe.old" -Force -ErrorAction SilentlyContinue
if (Test-Path $exe) {
    # a running exe is locked against overwrite but NOT against rename
    Move-Item $exe "$exe.old" -Force
}
Move-Item $tmp $exe -Force
Remove-Item "$exe.old" -Force -ErrorAction SilentlyContinue

& $exe --help | Out-Null
Write-Host "tessera: installed to $exe"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) {
    [Environment]::SetEnvironmentVariable("Path", $dir, "User")
    Write-Host "tessera: added $dir to your user PATH - open a NEW terminal for it to take effect."
} elseif ($userPath -notlike "*$dir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$dir", "User")
    Write-Host "tessera: added $dir to your user PATH - open a NEW terminal for it to take effect."
}

Write-Host ""
Write-Host "next:  tessera app     (opens the review UI; first run loads an offline sample)"
