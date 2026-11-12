param(
    [string]$ExePath = "",
    [string]$PythonExe = ".venv\Scripts\python.exe",
    [int]$WarmIterations = 3,
    [string]$OutputPath = "artifacts\phase1\evidence\startup_time.json"
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
        d2_note = if ($isCi) { "Measured on GitHub Actions virtual runner; non-normative per Decision D2 (reference: 8-core, 16 GB, NVMe, Win11 23H2)." } else { "Measured on local machine." }
        os_caption = [string]$os.Caption
        cpu_name = [string]$cpu.Name
        logical_processors = [int]$cs.NumberOfLogicalProcessors
        ram_gb = [math]::Round(($cs.TotalPhysicalMemory / 1GB), 2)
    }
}

function Resolve-TargetCommand {
    if ($ExePath -and (Test-Path $ExePath)) {
        return @{ FilePath = (Resolve-Path $ExePath).Path; PrefixArgs = @(); TargetKind = "pyinstaller_onedir_exe" }
    }
    $defaultExe = Join-Path $RepoRoot "dist\EnergyBaseline\EnergyBaseline.exe"
    if (Test-Path $defaultExe) {
        return @{ FilePath = (Resolve-Path $defaultExe).Path; PrefixArgs = @(); TargetKind = "pyinstaller_onedir_exe" }
    }
    $pyFull = Join-Path $RepoRoot $PythonExe
    if (-not (Test-Path $pyFull)) {
        $pyFull = (Get-Command python).Source
    }
    return @{ FilePath = $pyFull; PrefixArgs = @("-m", "energybaseline"); TargetKind = "python_module" }
}

function Invoke-TimedLaunch([hashtable]$Target, [string]$WorkDir, [string]$Label) {
    $telPath = Join-Path $WorkDir ("tel_" + $Label + ".json")
    $instKey = "eb_startup_" + $Label + "_" + [guid]::NewGuid().ToString("N").Substring(0, 6)
    $launchArgs = @($Target.PrefixArgs + @("--instance-key", $instKey, "--auto-close-ms", "120", "--telemetry-out", $telPath))

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process -FilePath $Target.FilePath -ArgumentList $launchArgs -WorkingDirectory $RepoRoot -PassThru
    $done = $p.WaitForExit(60000)
    $sw.Stop()
    if (-not $done) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        throw "Launch '$Label' timed out after 60 s."
    }
    if ($p.ExitCode -ne 0) {
        throw "Launch '$Label' exited with non-zero code $($p.ExitCode)."
    }
    $tel = Get-Content $telPath -Raw | ConvertFrom-Json
    return [ordered]@{
        label = $Label
        exit_code = [int]$p.ExitCode
        first_paint_ms = [double]$tel.first_paint_ms
        total_process_ms = [math]::Round($sw.Elapsed.TotalMilliseconds, 2)
        db_ok = [bool]$tel.db.ok
        db_rung = [string]$tel.db.rung
    }
}

$target = Resolve-TargetCommand
$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("eb_startup_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null

$prevEbHome = $env:EB_HOME
$prevNonInt = $env:EB_NONINTERACTIVE
$env:EB_HOME = Join-Path $workRoot "app_home"
$env:EB_NONINTERACTIVE = "1"

try {
    $cold = Invoke-TimedLaunch -Target $target -WorkDir $workRoot -Label "cold_1"
    $warmRuns = @()
    for ($i = 1; $i -le $WarmIterations; $i++) {
        $warmRuns += (Invoke-TimedLaunch -Target $target -WorkDir $workRoot -Label "warm_$i")
    }
    $sortedWarm = @($warmRuns | ForEach-Object { [double]$_.first_paint_ms } | Sort-Object)
    $mid = [int][math]::Floor($sortedWarm.Count / 2)
    $warmMedianMs = if ($sortedWarm.Count -gt 0) { $sortedWarm[$mid] } else { $cold.first_paint_ms }

    $report = [ordered]@{
        schema_version = "1.0"
        script = "scripts/evidence/startup_time.ps1"
        timestamp_utc = (Get-Date).ToUniversalTime().ToString("o")
        target_kind = $target.TargetKind
        target_executable = $target.FilePath
        hardware = Get-HardwareMeta
        cold_first_paint_ms = [double]$cold.first_paint_ms
        warm_first_paint_median_ms = [double]$warmMedianMs
        normative_budgets_ms = [ordered]@{
            cold_max_ms_on_d2_ref_hw = 4000.0
            warm_max_ms_on_d2_ref_hw = 1500.0
        }
        cold_run = $cold
        warm_runs = $warmRuns
        pass = ($cold.exit_code -eq 0 -and $cold.db_ok -and ($warmRuns | Where-Object { $_.exit_code -ne 0 }).Count -eq 0)
    }

    $outFull = if ([System.IO.Path]::IsPathRooted($OutputPath)) { $OutputPath } else { Join-Path $RepoRoot $OutputPath }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outFull) | Out-Null
    $json = $report | ConvertTo-Json -Depth 8
    Set-Content -Path $outFull -Value $json -Encoding UTF8
    Write-Output $json

    if (-not $report.pass) {
        exit 1
    }
}
finally {
    $env:EB_HOME = $prevEbHome
    $env:EB_NONINTERACTIVE = $prevNonInt
    Remove-Item -Recurse -Force $workRoot -ErrorAction SilentlyContinue
}
