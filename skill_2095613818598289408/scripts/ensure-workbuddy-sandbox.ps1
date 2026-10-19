# Idempotent WorkBuddy sandbox whitelist.
# Adds ~/.zagens-pro, and when npm is on PATH also the global prefix and cache
# so `npm install -g` can write inside the sandbox.
# Does not overwrite skills; only touches sandbox.extraAllowWrite.
# Test hook: set ZAGENS_WORKBUDDY_HOME to a fake home that contains .workbuddy\settings.json.

$ErrorActionPreference = 'Stop'

function Write-Result {
    param(
        [bool]$Ok,
        [string]$Status,
        [string]$Path,
        [string]$Message
    )
    $payload = [ordered]@{
        ok      = $Ok
        status  = $Status
        path    = $Path
        message = $Message
    }
    $payload | ConvertTo-Json -Compress
}

function Get-UserHome {
    if ($env:ZAGENS_WORKBUDDY_HOME) { return $env:ZAGENS_WORKBUDDY_HOME }
    if ($env:USERPROFILE) { return $env:USERPROFILE }
    if ($env:HOME) { return $env:HOME }
    return $null
}

function Get-NpmConfigValue {
    param([string]$Key)
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { return $null }
    $out = & npm config get $Key 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $lines = @(($out | Out-String).Trim() -split "[\r\n]+" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -eq 0) { return $null }
    $text = $lines[-1].Trim().TrimEnd('\', '/')
    if ($text -eq 'undefined' -or $text -eq 'null') { return $null }
    return $text
}

function Test-SameEntry {
    param([string]$A, [string]$B)
    $na = ([string]$A).Trim().TrimEnd('\', '/')
    $nb = ([string]$B).Trim().TrimEnd('\', '/')
    return [string]::Equals($na, $nb, [System.StringComparison]::OrdinalIgnoreCase)
}

$userHome = Get-UserHome
if (-not $userHome) {
    Write-Result -Ok $false -Status 'error' -Path '' -Message 'USERPROFILE/HOME not set'
    exit 1
}

$wanted = New-Object System.Collections.Generic.List[string]
$wanted.Add('~/.zagens-pro') | Out-Null
$npmNote = $null
$prefix = Get-NpmConfigValue 'prefix'
$cache = Get-NpmConfigValue 'cache'
if ($prefix) {
    $dup = $false
    foreach ($e in $wanted) { if (Test-SameEntry $e $prefix) { $dup = $true; break } }
    if (-not $dup) { $wanted.Add($prefix) | Out-Null }
}
if ($cache) {
    $dup = $false
    foreach ($e in $wanted) { if (Test-SameEntry $e $cache) { $dup = $true; break } }
    if (-not $dup) { $wanted.Add($cache) | Out-Null }
}
if (-not $prefix -and -not $cache) {
    $npmNote = 'npm not on PATH; prefix and cache not added — re-run after Node.js is available'
}

$settingsPath = Join-Path $userHome '.workbuddy\settings.json'
if (-not (Test-Path -LiteralPath $settingsPath)) {
    Write-Result -Ok $true -Status 'skipped' -Path $settingsPath -Message 'WorkBuddy settings.json not found; open WorkBuddy once, then re-run'
    exit 0
}

try {
    $raw = [System.IO.File]::ReadAllText($settingsPath)
    if ([string]::IsNullOrWhiteSpace($raw)) {
        Write-Result -Ok $false -Status 'error' -Path $settingsPath -Message 'settings.json is empty'
        exit 1
    }

    $settings = $raw | ConvertFrom-Json
    if ($null -eq $settings -or $settings -is [System.Object[]]) {
        Write-Result -Ok $false -Status 'error' -Path $settingsPath -Message 'settings.json root must be an object'
        exit 1
    }

    if (-not $settings.PSObject.Properties['sandbox'] -or $null -eq $settings.sandbox) {
        $settings | Add-Member -NotePropertyName sandbox -NotePropertyValue ([pscustomobject]@{}) -Force
    }
    if (-not $settings.sandbox.PSObject.Properties['extraAllowWrite'] -or $null -eq $settings.sandbox.extraAllowWrite) {
        $settings.sandbox | Add-Member -NotePropertyName extraAllowWrite -NotePropertyValue @() -Force
    }

    $allow = @($settings.sandbox.extraAllowWrite | Where-Object { $_ -is [string] -or $_ -is [System.ValueType] })
    $missing = New-Object System.Collections.Generic.List[string]
    foreach ($entry in $wanted) {
        $found = $false
        foreach ($have in $allow) {
            if (Test-SameEntry ([string]$have) $entry) { $found = $true; break }
        }
        if (-not $found) { $missing.Add($entry) | Out-Null }
    }

    if ($missing.Count -eq 0) {
        $msg = 'sandbox.extraAllowWrite already contains ' + ($wanted -join ', ')
        if ($npmNote) { $msg = $msg + '; ' + $npmNote }
        Write-Result -Ok $true -Status 'already' -Path $settingsPath -Message $msg
        exit 0
    }

    $stamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $backup = Join-Path (Split-Path -Parent $settingsPath) "settings.json.bak-zagens-$stamp"
    Copy-Item -LiteralPath $settingsPath -Destination $backup -Force

    $settings.sandbox.extraAllowWrite = @($allow + $missing)
    $json = $settings | ConvertTo-Json -Depth 20
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($settingsPath, $json + "`n", $utf8NoBom)

    $msg = 'added ' + ($missing -join ', ') + " (backup $backup)"
    if ($npmNote) { $msg = $msg + '; ' + $npmNote }
    Write-Result -Ok $true -Status 'added' -Path $settingsPath -Message $msg
    exit 0
}
catch {
    Write-Result -Ok $false -Status 'error' -Path $settingsPath -Message $_.Exception.Message
    exit 1
}
