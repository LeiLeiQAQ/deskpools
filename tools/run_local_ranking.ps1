# Compatibility entry point: ranking now runs inside the website backend.
[CmdletBinding()]
param([int]$RankPort = 8070)
& (Join-Path $PSScriptRoot 'run_local_backend.ps1') -Port $RankPort -LaunchClient
