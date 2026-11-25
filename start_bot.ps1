# Start the Telegram bot without disrupting Unity or other studio jobs.
# Usage: powershell -ExecutionPolicy Bypass -File .\start_bot.ps1
param(
    [ValidateRange(0,3600)][int]$WaitSeconds = 120,
    [ValidateRange(1,30)][int]$PollSeconds = 3
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$bot = Join-Path $root 'telegram_bot.py'
$lockPath = Join-Path $root '.studio_cache\controller.lock'

if (-not (Test-Path -LiteralPath $python)) { throw "Python environment not found: $python" }
if (-not (Test-Path -LiteralPath $bot)) { throw "Bot not found: $bot" }

function Get-StudioBotProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match '(?i)(^|[\\/\s"''])telegram_bot\.py([\s"'']|$)'
        })
}

function Test-ControllerLock {
    # Probe the same first byte used by Python's msvcrt.locking(..., 1).
    # File existence is NOT proof of ownership. Never remove the lock file.
    $stream = $null
    $locked = $false
    try {
        $stream = [System.IO.File]::Open($lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::ReadWrite)
        $stream.Lock(0, 1)
        $locked = $true
        return $true
    }
    catch [System.IO.IOException] {
        return $false
    }
    catch [System.UnauthorizedAccessException] {
        throw "Access denied to $lockPath. Check Windows permissions; do not delete the lock. $($_.Exception.Message)"
    }
    finally {
        if ($stream) {
            if ($locked) { $stream.Unlock(0, 1) }
            $stream.Dispose()
        }
    }
}

Set-Location -LiteralPath $root
$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ($true) {
    $bots = @(Get-StudioBotProcesses)
    if ($bots.Count -gt 0) {
        Write-Host "Telegram bot is already running (PID: $(($bots.ProcessId -join ', '))). Not starting another."
        exit 0
    }

    if (Test-ControllerLock) { break }

    if ((Get-Date) -ge $deadline) {
        Write-Warning "controller.lock is still held after $WaitSeconds seconds. Another studio operation may be active. Nothing was stopped or deleted."
        Write-Host 'Check active studio processes with:'
        Write-Host 'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "main.py|telegram_bot.py" } | Select-Object ProcessId,CommandLine'
        exit 2
    }
    Write-Host "Controller busy; waiting $PollSeconds seconds (no processes will be stopped)..."
    Start-Sleep -Seconds $PollSeconds
}

Write-Host 'Starting Telegram bot. Press Ctrl+C to stop it.'
# The bot itself acquires the lock; a simultaneous launch can still lose the race safely.
& $python $bot
exit $LASTEXITCODE


Stop-Process -Id 26860 -Force