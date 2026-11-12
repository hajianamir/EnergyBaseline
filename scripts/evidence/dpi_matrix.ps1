param(
    [string]$ExePath = "",
    [string]$PythonExe = ".venv\Scripts\python.exe",
    [string]$OutputPath = "artifacts\phase1\evidence\dpi_matrix.json"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

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

$target = Resolve-TargetCommand
$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("eb_dpi_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null

$prevEbHome = $env:EB_HOME
$prevNonInt = $env:EB_NONINTERACTIVE
$prevScale = $env:QT_SCALE_FACTOR
$env:EB_HOME = Join-Path $workRoot "app_home"
$env:EB_NONINTERACTIVE = "1"

try {
    $scales = @(
        @{ pct = 100; factor = "1.0" },
        @{ pct = 125; factor = "1.25" },
        @{ pct = 150; factor = "1.5" },
        @{ pct = 175; factor = "1.75" }
    )
    $results = @()

    foreach ($s in $scales) {
        $env:QT_SCALE_FACTOR = $s.factor
        $telFile = Join-Path $workRoot ("dpi_" + $s.pct + ".json")
        $instKey = "eb_dpi_" + $s.pct + "_" + [guid]::NewGuid().ToString("N").Substring(0, 6)
        $launchArgs = @($target.PrefixArgs + @("--instance-key", $instKey, "--auto-close-ms", "120", "--telemetry-out", $telFile))

        $proc = Start-Process -FilePath $target.FilePath -ArgumentList $launchArgs -WorkingDirectory $RepoRoot -PassThru
        $done = $proc.WaitForExit(60000)
        if (-not $done) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            throw "DPI matrix launch at $($s.pct)% timed out."
        }
        $tel = Get-Content $telFile -Raw | ConvertFrom-Json
        $geom = $tel.window_geometry
        $rowOk = ($proc.ExitCode -eq 0 -and [bool]$geom.fits_width -and [bool]$geom.fits_height)
        $results += [ordered]@{
            requested_scale_percent = [int]$s.pct
            requested_qt_scale_factor = [double]$s.factor
            reported_device_pixel_ratio = [double]$tel.dpi_scale
            exit_code = [int]$proc.ExitCode
            first_paint_ms = [double]$tel.first_paint_ms
            window_geometry = $geom
            pass = $rowOk
        }
    }

    $isCi = ($env:GITHUB_ACTIONS -eq "true")
    $allPass = (@($results | Where-Object { -not $_.pass }).Count -eq 0)
    $report = [ordered]@{
        schema_version = "1.0"
        script = "scripts/evidence/dpi_matrix.ps1"
        timestamp_utc = (Get-Date).ToUniversalTime().ToString("o")
        environment_label = if ($isCi) { "CI, non-normative" } else { "Local Windows host" }
        d2_note = if ($isCi) { "Synthetic QT_SCALE_FACTOR sweep on GitHub Actions virtual display; non-normative per Decision D2 (physical 1920x1080 Windows 11 display scaling remains required for human visual sign-off)." } else { "Measured on local Windows display." }
        target_kind = $target.TargetKind
        target_executable = $target.FilePath
        scales = $results
        pass = $allPass
    }

    $outFull = if ([System.IO.Path]::IsPathRooted($OutputPath)) { $OutputPath } else { Join-Path $RepoRoot $OutputPath }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outFull) | Out-Null
    $json = $report | ConvertTo-Json -Depth 8
    Set-Content -Path $outFull -Value $json -Encoding UTF8
    Write-Output $json

    if (-not $allPass) {
        exit 1
    }
}
finally {
    $env:EB_HOME = $prevEbHome
    $env:EB_NONINTERACTIVE = $prevNonInt
    $env:QT_SCALE_FACTOR = $prevScale
    Remove-Item -Recurse -Force $workRoot -ErrorAction SilentlyContinue
}
