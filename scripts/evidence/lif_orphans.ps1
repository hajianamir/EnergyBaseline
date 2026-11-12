param(
    [string]$ExePath = "",
    [string]$PythonExe = ".venv\Scripts\python.exe",
    [string]$OutputPath = "artifacts\phase1\evidence\lif_orphans.json"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Get-HardwareMeta {
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $cs = Get-CimInstance Win32_ComputerSystem
    $os = Get-CimInstance Win32_OperatingSystem
    $isCi = ($env:GITHUB_ACTIONS -eq "true")
    return [ordered]@{
        environment_label = if ($isCi) { "CI, non-normative" } else { "Local Windows host" }
        is_ci = $isCi
        normative_per_d2 = (-not $isCi)
        os_caption = [string]$os.Caption
        os_build = [string]$os.BuildNumber
        cpu_name = [string]$cpu.Name
        logical_processors = [int]$cs.NumberOfLogicalProcessors
        ram_gb = [math]::Round(($cs.TotalPhysicalMemory / 1GB), 2)
    }
}

function Resolve-TargetCommand {
    if ($ExePath -and (Test-Path $ExePath)) {
        return @{ FilePath = (Resolve-Path $ExePath).Path; PrefixArgs = @(); TargetKind = "pyinstaller_exe" }
    }
    $defaultExe = Join-Path $RepoRoot "dist\EnergyBaseline\EnergyBaseline.exe"
    if (Test-Path $defaultExe) {
        return @{ FilePath = (Resolve-Path $defaultExe).Path; PrefixArgs = @(); TargetKind = "pyinstaller_exe" }
    }
    $pyFull = Join-Path $RepoRoot $PythonExe
    if (-not (Test-Path $pyFull)) {
        $pyFull = (Get-Command python).Source
    }
    return @{ FilePath = $pyFull; PrefixArgs = @("-m", "energybaseline"); TargetKind = "python_module" }
}

function Test-PidAlive([int]$TargetPid) {
    $p = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
    return ($null -ne $p -and -not $p.HasExited)
}

$target = Resolve-TargetCommand
$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("eb_lif_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null

$prevEbHome = $env:EB_HOME
$prevNonInt = $env:EB_NONINTERACTIVE
$env:EB_HOME = Join-Path $workRoot "app_home"
$env:EB_NONINTERACTIVE = "1"

try {
    # Scenario 1: Clean quit (aboutToQuit -> cancel -> join 5 s -> hard stop -> WAL checkpoint -> DB close)
    $telClean = Join-Path $workRoot "telemetry_clean.json"
    $instClean = "eb_lif_clean_" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    $argsClean = @($target.PrefixArgs + @("--instance-key", $instClean, "--spawn-worker-seconds", "25", "--auto-close-ms", "600", "--telemetry-out", $telClean))

    $procClean = Start-Process -FilePath $target.FilePath -ArgumentList $argsClean -WorkingDirectory $RepoRoot -PassThru
    $exitedClean = $procClean.WaitForExit(35000)
    if (-not $exitedClean) {
        Stop-Process -Id $procClean.Id -Force -ErrorAction SilentlyContinue
        throw "Clean-quit scenario timed out waiting for parent exit."
    }

    $tSw1 = [System.Diagnostics.Stopwatch]::StartNew()
    $cleanData = Get-Content $telClean -Raw | ConvertFrom-Json
    $cleanWorkerPids = @($cleanData.spawned_worker_pids | ForEach-Object { [int]$_ })
    while ($tSw1.Elapsed.TotalSeconds -lt 5.0) {
        $alive1 = @($cleanWorkerPids | Where-Object { Test-PidAlive $_ })
        if ($alive1.Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    }
    $tSw1.Stop()
    $survivingClean = @($cleanWorkerPids | Where-Object { Test-PidAlive $_ })

    # Scenario 2: Abrupt parent termination (Windows Job Object KILL_ON_JOB_CLOSE verification)
    $telKill = Join-Path $workRoot "telemetry_kill.json"
    $instKill = "eb_lif_kill_" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    $argsKill = @($target.PrefixArgs + @("--instance-key", $instKill, "--spawn-worker-seconds", "60", "--ignore-cancel", "--telemetry-out", $telKill))

    $procKill = Start-Process -FilePath $target.FilePath -ArgumentList $argsKill -WorkingDirectory $RepoRoot -PassThru
    $waitTel = [System.Diagnostics.Stopwatch]::StartNew()
    while (-not (Test-Path $telKill) -and $waitTel.Elapsed.TotalSeconds -lt 30.0) {
        if ($procKill.HasExited) { break }
        Start-Sleep -Milliseconds 150
    }
    if (-not (Test-Path $telKill)) {
        Stop-Process -Id $procKill.Id -Force -ErrorAction SilentlyContinue
        throw "Abrupt-kill scenario failed to emit live worker telemetry within 30 s."
    }

    $killData = Get-Content $telKill -Raw | ConvertFrom-Json
    $killWorkerPids = @($killData.spawned_worker_pids | ForEach-Object { [int]$_ })
    $cimChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($procKill.Id)" | Select-Object -ExpandProperty ProcessId)
    $trackedKillPids = @($killWorkerPids + $cimChildren | Select-Object -Unique)

    $tSw2 = [System.Diagnostics.Stopwatch]::StartNew()
    Stop-Process -Id $procKill.Id -Force -ErrorAction SilentlyContinue
    $procKill.WaitForExit(5000) | Out-Null

    while ($tSw2.Elapsed.TotalSeconds -lt 5.0) {
        $alive2 = @($trackedKillPids | Where-Object { Test-PidAlive $_ })
        if ($alive2.Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    }
    $tSw2.Stop()
    $survivingKill = @($trackedKillPids | Where-Object { Test-PidAlive $_ })

    $totalOrphans = $survivingClean.Count + $survivingKill.Count
    $passed = (
        $procClean.ExitCode -eq 0 -and
        $cleanWorkerPids.Count -ge 1 -and
        $killWorkerPids.Count -ge 1 -and
        [bool]$killData.job_object_active -and
        $totalOrphans -eq 0 -and
        $tSw1.Elapsed.TotalSeconds -le 5.0 -and
        $tSw2.Elapsed.TotalSeconds -le 5.0
    )

    $report = [ordered]@{
        schema_version = "1.0"
        script = "scripts/evidence/lif_orphans.ps1"
        timestamp_utc = (Get-Date).ToUniversalTime().ToString("o")
        target_kind = $target.TargetKind
        target_executable = $target.FilePath
        hardware = Get-HardwareMeta
        clean_quit_scenario = [ordered]@{
            parent_exit_code = [int]$procClean.ExitCode
            spawned_worker_pids = $cleanWorkerPids
            cleanup_elapsed_seconds = [math]::Round($tSw1.Elapsed.TotalSeconds, 4)
            surviving_orphan_pids = $survivingClean
            wal_checkpointed = [bool]$cleanData.shutdown.db.wal_checkpointed
        }
        abrupt_kill_scenario = [ordered]@{
            job_object_active = [bool]$killData.job_object_active
            job_object_assigned_pids = @($killData.job_object_assigned_pids)
            tracked_child_pids = $trackedKillPids
            cleanup_elapsed_seconds = [math]::Round($tSw2.Elapsed.TotalSeconds, 4)
            surviving_orphan_pids = $survivingKill
        }
        orphan_count = $totalOrphans
        max_allowed_seconds = 5.0
        pass = $passed
    }

    $outFull = if ([System.IO.Path]::IsPathRooted($OutputPath)) { $OutputPath } else { Join-Path $RepoRoot $OutputPath }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outFull) | Out-Null
    $json = $report | ConvertTo-Json -Depth 8
    Set-Content -Path $outFull -Value $json -Encoding UTF8
    Write-Output $json

    if (-not $passed) {
        exit 1
    }
}
finally {
    $env:EB_HOME = $prevEbHome
    $env:EB_NONINTERACTIVE = $prevNonInt
    Remove-Item -Recurse -Force $workRoot -ErrorAction SilentlyContinue
}
