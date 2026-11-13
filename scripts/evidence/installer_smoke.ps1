param(
    [string]$Profile = "lite",
    [string]$PythonExe = ".venv\Scripts\python.exe",
    [string]$DistDir = "dist",
    [string]$OutputPath = "artifacts\phase1\evidence\installer_smoke.json"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$distFull = if ([System.IO.Path]::IsPathRooted($DistDir)) { $DistDir } else { Join-Path $RepoRoot $DistDir }
$pyFull = Join-Path $RepoRoot $PythonExe
if (-not (Test-Path $pyFull)) {
    $pyFull = (Get-Command python).Source
}

$installerPath = Join-Path $distFull "EnergyBaseline-Setup-$Profile.exe"
$bundleExe = Join-Path $distFull "EnergyBaseline\EnergyBaseline.exe"

if (-not (Test-Path $installerPath) -or -not (Test-Path $bundleExe)) {
    & $pyFull (Join-Path $RepoRoot "scripts\build_exe.py") --profile $Profile --installer --dist-dir $distFull
    if ($LASTEXITCODE -ne 0) {
        throw "build_exe.py --profile $Profile --installer failed with exit code $LASTEXITCODE"
    }
}

$sbomPath = Join-Path $distFull "EnergyBaseline\sbom.cdx.json"
$lnkPath = Join-Path $distFull "EnergyBaseline\EnergyBaseline.lnk"
$internalDir = Join-Path $distFull "EnergyBaseline\_internal"

if (-not (Test-Path $bundleExe)) { throw "Missing onedir executable: $bundleExe" }
if (-not (Test-Path $internalDir)) { throw "Missing PyInstaller onedir _internal directory: $internalDir" }
if (-not (Test-Path $sbomPath)) { throw "Missing CycloneDX SBOM: $sbomPath" }
if (-not (Test-Path $lnkPath)) { throw "Missing Windows .lnk shortcut: $lnkPath" }
if (-not (Test-Path $installerPath)) { throw "Missing compiled per-user installer: $installerPath" }

$sbomJson = Get-Content $sbomPath -Raw | ConvertFrom-Json
$installRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("eb_per_user_inst_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$appDataRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("eb_per_user_home_" + [guid]::NewGuid().ToString("N").Substring(0, 8))

$prevEbHome = $env:EB_HOME
$prevNonInt = $env:EB_NONINTERACTIVE
$env:EB_HOME = $appDataRoot
$env:EB_NONINTERACTIVE = "1"

try {
    # 1. Silent per-user install (no elevation)
    $instArgs = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/DIR=`"$installRoot`"")
    $instProc = Start-Process -FilePath $installerPath -ArgumentList $instArgs -Wait -PassThru
    if ($instProc.ExitCode -ne 0) {
        # NSIS fallback flag syntax if NSIS was used
        $nsisArgs = @("/S", "/D=$installRoot")
        $instProc = Start-Process -FilePath $installerPath -ArgumentList $nsisArgs -Wait -PassThru
    }

    $installedExe = Join-Path $installRoot "EnergyBaseline.exe"
    $installedExists = Test-Path $installedExe
    if (-not $installedExists) {
        throw "Installed EnergyBaseline.exe was not found under $installRoot (installer exit=$($instProc.ExitCode))"
    }

    # 2. Launch installed EnergyBaseline.exe and verify DB + first paint + clean shutdown
    $telFile = Join-Path $appDataRoot "installed_boot.json"
    $instKey = "eb_inst_smoke_" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    $bootProc = Start-Process -FilePath $installedExe -ArgumentList @("--instance-key", $instKey, "--auto-close-ms", "150", "--telemetry-out", $telFile) -PassThru
    $bootDone = $bootProc.WaitForExit(60000)
    if (-not $bootDone) {
        Stop-Process -Id $bootProc.Id -Force -ErrorAction SilentlyContinue
        throw "Installed EnergyBaseline.exe timed out during smoke launch."
    }
    $bootData = Get-Content $telFile -Raw | ConvertFrom-Json

    # 3. Silent per-user uninstall
    $uninstExe = Join-Path $installRoot "unins000.exe"
    $uninstKind = "inno_setup"
    $uninstArgs = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    if (-not (Test-Path $uninstExe)) {
        $uninstExe = Join-Path $installRoot "Uninstall.exe"
        $uninstKind = "nsis"
        $uninstArgs = @("/S")
    }
    if (-not (Test-Path $uninstExe)) {
        throw "No uninstaller found in $installRoot"
    }
    $unProc = Start-Process -FilePath $uninstExe -ArgumentList $uninstArgs -Wait -PassThru

    $waitUn = [System.Diagnostics.Stopwatch]::StartNew()
    while ((Test-Path $installedExe) -and $waitUn.Elapsed.TotalSeconds -lt 10.0) {
        Start-Sleep -Milliseconds 200
    }
    $removedCleanly = -not (Test-Path $installedExe)

    $isCi = ($env:GITHUB_ACTIONS -eq "true")
    $report = [ordered]@{
        schema_version = "1.0"
        script = "scripts/evidence/installer_smoke.ps1"
        timestamp_utc = (Get-Date).ToUniversalTime().ToString("o")
        environment_label = if ($isCi) { "CI, non-normative" } else { "Local Windows host" }
        profile = $Profile
        layout = "onedir"
        installer_engine = $uninstKind
        artifacts = [ordered]@{
            onedir_exe = $bundleExe
            onedir_exe_sha256 = (Get-FileHash -Algorithm SHA256 $bundleExe).Hash.ToLowerInvariant()
            shortcut_lnk = $lnkPath
            shortcut_sha256 = (Get-FileHash -Algorithm SHA256 $lnkPath).Hash.ToLowerInvariant()
            sbom_path = $sbomPath
            sbom_sha256 = (Get-FileHash -Algorithm SHA256 $sbomPath).Hash.ToLowerInvariant()
            sbom_component_count = @($sbomJson.components).Count
            installer_exe = $installerPath
            installer_sha256 = (Get-FileHash -Algorithm SHA256 $installerPath).Hash.ToLowerInvariant()
        }
        install_smoke = [ordered]@{
            install_exit_code = [int]$instProc.ExitCode
            installed_exe_verified = $installedExists
            launch_exit_code = [int]$bootProc.ExitCode
            launch_db_ok = [bool]$bootData.db.ok
            first_paint_ms = [double]$bootData.first_paint_ms
            uninstall_exit_code = [int]$unProc.ExitCode
            uninstalled_cleanly = $removedCleanly
        }
        pass = (
            $instProc.ExitCode -eq 0 -and
            $installedExists -and
            $bootProc.ExitCode -eq 0 -and
            [bool]$bootData.db.ok -and
            $unProc.ExitCode -eq 0 -and
            $removedCleanly
        )
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
    Remove-Item -Recurse -Force $installRoot -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $appDataRoot -ErrorAction SilentlyContinue
}
