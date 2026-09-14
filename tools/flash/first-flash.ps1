param([string]$Port, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
try {
  Set-Location $PSScriptRoot
  $entries = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'FILES.json') | ConvertFrom-Json
  foreach ($entry in $entries) {
    if ([IO.Path]::IsPathRooted($entry.path) -or $entry.path -match '(^|[\\/])\.\.([\\/]|$)') { throw 'Invalid manifest path' }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PSScriptRoot $entry.path)).Hash
    if ($actual -ne $entry.sha256) { throw "Checksum failed: $($entry.path)" }
  }
  foreach ($required in @('factory.bin', 'FLASH.json', 'first-flash.ps1', 'first_flash.py', 'esptool.exe')) {
    if ($required -notin $entries.path) { throw "Incomplete package: $required" }
  }
  $info = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'FLASH.json') | ConvertFrom-Json
  if ($info.chip -ne 'esp32s3' -or $info.flash_size -ne 16777216 -or $info.offset -ne '0x0' -or $info.erase_all -ne $true) { throw 'Unsupported target' }
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PSScriptRoot 'factory.bin')).Hash -ne $info.sha256) { throw 'Factory image mismatch' }
  Write-Host 'LILYGO T-Display-S3 AMOLED non-touch / 16MB ONLY.'
  Write-Host 'FIRST INSTALL: erases ALL flash, including Wi-Fi and display settings.' -ForegroundColor Yellow
  if (-not $Port) {
    Write-Host ('Available ports: ' + ([System.IO.Ports.SerialPort]::GetPortNames() -join ', '))
    $Port = Read-Host 'Device port (example COM5)'
  }
  $Port = $Port.Trim().ToUpperInvariant()
  if ($Port -notmatch '^COM[0-9]+$') { throw 'Invalid COM port' }
  $probeArgs = @('--chip', 'esp32s3', '--port', $Port, '--baud', '460800', 'flash_id')
  $writeArgs = @('--chip', 'esp32s3', '--port', $Port, '--baud', '460800', 'write_flash', '--flash_mode', 'keep', '--flash_freq', 'keep', '--flash_size', 'keep', '--erase-all', '0x0', (Join-Path $PSScriptRoot 'factory.bin'))
  if ($DryRun) {
    Write-Host ('esptool.exe ' + ($probeArgs -join ' '))
    Write-Host ('esptool.exe ' + ($writeArgs -join ' '))
    exit 0
  }
  if ((Read-Host 'Type ERASE to continue') -cne 'ERASE') { Write-Host 'Cancelled; device not accessed.'; exit 1 }
  $output = & (Join-Path $PSScriptRoot 'esptool.exe') @probeArgs 2>&1
  $result = $LASTEXITCODE
  $output | ForEach-Object { Write-Host $_ }
  if ($result -ne 0) { throw 'Chip/flash detection failed; no erase/write performed.' }
  if (($output -join "`n") -notmatch 'Detected flash size:\s*16\s*MB\b') { throw 'Expected 16MB flash; no erase/write performed.' }
  & (Join-Path $PSScriptRoot 'esptool.exe') @writeArgs
  if ($LASTEXITCODE -ne 0) { throw 'Flash failed. Close serial monitor and retry in BOOT mode.' }
  Write-Host 'SUCCESS. Release BOOT and press RESET. Configure USB sending in NAS Display.' -ForegroundColor Green
} catch { Write-Host $_ -ForegroundColor Red; exit 1 }
