param(
    [Parameter(Mandatory = $true)]
    [string]$VpsHost,

    [string]$VpsUser = "Administrator",

    [int]$Port = 22,

    [string]$RemoteJsonPath = "C:/trade-export/out/apolloes-hermes-live-trades.json",

    [string]$LocalJsonPath = "C:\VScode\Reports\Live\apolloes-hermes-live-trades.json",

    [string]$KeyPath = "",

    [string]$LogPath = "C:\VScode\Reports\Live\logs\pull_vps_apolloes_hermes_json.log",

    [int]$MaxStaleMinutes = 5
)

$ErrorActionPreference = "Stop"

function Write-LogLine {
    param(
        [string]$Message
    )

    $logDirectory = Split-Path -Path $LogPath -Parent
    if (-not (Test-Path $logDirectory)) {
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    }

    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $LogPath -Value ("[{0}] {1}" -f $timestamp, $Message)
}

try {
    $destinationDirectory = Split-Path -Path $LocalJsonPath -Parent
    if (-not (Test-Path $destinationDirectory)) {
        New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
    }

    $scpArgs = @("-P", $Port.ToString())
    if ($KeyPath) {
        $scpArgs += @("-i", $KeyPath)
    }

    $scpArgs += @(
        ("{0}@{1}:{2}" -f $VpsUser, $VpsHost, $RemoteJsonPath),
        $LocalJsonPath
    )

    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) {
        throw "scp failed with exit code $LASTEXITCODE"
    }

    $payload = Get-Content -Path $LocalJsonPath -Raw | ConvertFrom-Json
    $generatedAtUtc = $payload.generated_at_utc
    if (-not $generatedAtUtc) {
        throw "Pulled JSON is missing generated_at_utc"
    }

    $generatedAt = [datetime]::Parse($generatedAtUtc, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
    $age = (Get-Date).ToUniversalTime() - $generatedAt.ToUniversalTime()
    if ($age.TotalMinutes -gt $MaxStaleMinutes) {
        throw ("Pulled JSON is stale by {0:N1} minutes (generated_at_utc={1})" -f $age.TotalMinutes, $generatedAtUtc)
    }

    $successMessage = "SUCCESS | Pulled VPS JSON to $LocalJsonPath from $VpsUser@$VpsHost`:$RemoteJsonPath"
    Write-LogLine -Message $successMessage
    Write-Output $successMessage
}
catch {
    $errorMessage = "FAILURE | $($_.Exception.Message)"
    Write-LogLine -Message $errorMessage
    throw
}
