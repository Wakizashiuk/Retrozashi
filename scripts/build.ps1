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

Write-Host "==> MCP handshake with the windowed exe (as the Claude desktop app runs it)" -ForegroundColor Cyan
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = (Resolve-Path "dist\Retrozashi\Retrozashi.exe").Path
$psi.Arguments = "--mcp"
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$mcp = [System.Diagnostics.Process]::Start($psi)
$mcp.StandardInput.WriteLine('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ci"}}}')
$mcp.StandardInput.WriteLine('{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
$mcp.StandardInput.Close()
$reply = $mcp.StandardOutput.ReadToEnd()
if (-not $mcp.WaitForExit(60000)) { $mcp.Kill(); throw "MCP server did not exit" }
$lines = $reply -split "`n" | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json }
if ($lines.Count -ne 2 -or $lines[0].result.serverInfo.name -ne "retrozashi" -or $lines[1].result.tools.Count -lt 20) {
    throw "MCP handshake failed: $reply"
}
Write-Host "MCP OK: $($lines[1].result.tools.Count) tools"

Write-Host "==> Claude app check (--doctor) runs the packaged exe's own handshake" -ForegroundColor Cyan
$doctor = Join-Path $env:TEMP "retrozashi-doctor.txt"
Start-Process "dist\Retrozashi\Retrozashi.exe" -ArgumentList "--doctor", "`"$doctor`"" -Wait | Out-Null
Get-Content $doctor
if (-not (Select-String -Path $doctor -Pattern "starts and answers like it should" -Quiet)) { throw "Doctor handshake failed" }

Write-Host "==> Zipping" -ForegroundColor Cyan
Compress-Archive -Path "dist\Retrozashi" -DestinationPath "dist\Retrozashi-win64.zip" -Force
$size = "{0:N0} MB" -f ((Get-Item "dist\Retrozashi-win64.zip").Length / 1MB)
Write-Host "Done: dist\Retrozashi\Retrozashi.exe  (zip: $size)" -ForegroundColor Green
