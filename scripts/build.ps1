<#
.SYNOPSIS
  Build dist\Retrozashi\Retrozashi.exe and dist\Retrozashi-win64.zip, then self-test the bundle.

.EXAMPLE
  .\scripts\build.ps1            # full build (voice + UI Automation)
  .\scripts\build.ps1 -NoVoice   # ~200 MB smaller, no speech-to-text
#>
param([switch]$NoVoice)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$extras = if ($NoVoice) { "uia,build" } else { "voice,uia,build" }
Write-Host "==> Installing retrozashi[$extras]" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -e ".[$extras]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Running PyInstaller" -ForegroundColor Cyan
python -m PyInstaller packaging\Retrozashi.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "==> Self-testing the frozen exe" -ForegroundColor Cyan
$report = Join-Path $PWD "dist\selftest.json"
$proc = Start-Process "dist\Retrozashi\Retrozashi.exe" -ArgumentList "--self-test", "`"$report`"" -Wait -PassThru
Get-Content $report
$failed = (Get-Content $report | ConvertFrom-Json).PSObject.Properties |
    Where-Object { $_.Value -like "FAIL*" -and -not ($NoVoice -and $_.Name -eq "voice") }
if ($failed) { throw "Self-test failed: $($failed.Name -join ', ')" }

Write-Host "==> Zipping" -ForegroundColor Cyan
Compress-Archive -Path "dist\Retrozashi" -DestinationPath "dist\Retrozashi-win64.zip" -Force
$size = "{0:N0} MB" -f ((Get-Item "dist\Retrozashi-win64.zip").Length / 1MB)
Write-Host "Done: dist\Retrozashi\Retrozashi.exe  (zip: $size)" -ForegroundColor Green
