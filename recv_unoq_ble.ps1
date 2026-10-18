# Receive UNO Q sensor JSON over Bluetooth (no USB required).
# Usage:  .\recv_unoq_ble.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Python not found at $py" -ForegroundColor Red
  exit 1
}
& $py -m pip install -q bleak
Write-Host "Listening for AgriDrone-UNOQ over BLE (auto-reconnect)..." -ForegroundColor Green
Write-Host "Leave this open across board power-cycles. Do NOT pair in Windows." -ForegroundColor Yellow
& $py -u -m ground_station.bt_sensor_recv --address 14:B5:CD:E4:29:79 --timeout 15
