<#
.SYNOPSIS
    Authenticode signing helper and CI self-signed verification script (Ratified D1 = B).

.DESCRIPTION
    Production usage (requires hardware token/HSM or PFX before external distribution per D1 trigger):
        pwsh -NoProfile -File scripts/sign/sign_artifacts.ps1 -Files dist/EnergyBaseline.exe -PfxPath $env:SIGN_PFX -PfxPassword $env:SIGN_PWD -TimestampUrl http://timestamp.digicert.com -Verify

    CI smoke self-test (verifies signtool sign -> signtool verify /pa with an ephemeral self-signed certificate):
        pwsh -NoProfile -File scripts/sign/sign_artifacts.ps1 -SelfTest
#>
[CmdletBinding()]
param(
    [string[]]$Files = @(),
    [string]$PfxPath = "",
    [string]$PfxPassword = "",
    [string]$TimestampUrl = "",
    [switch]$Verify,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"

function Find-SignTool {
    $cmd = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    $kitsRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (Test-Path $kitsRoot) {
        $found = Get-ChildItem -Path $kitsRoot -Filter "signtool.exe" -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($found) {
            return $found.FullName
        }
    }
    throw "signtool.exe not found in PATH or Windows Kits\10\bin."
}

function Invoke-SignAndVerify {
    param(
        [string]$SignTool,
        [string[]]$TargetFiles,
        [string]$CertPfx,
        [string]$CertPassword,
        [string]$TsUrl,
        [bool]$DoVerify
    )
    foreach ($f in $TargetFiles) {
        if (-not (Test-Path $f)) {
            throw "Target file does not exist: $f"
        }
        $signArgs = @("sign", "/fd", "SHA256", "/f", $CertPfx, "/p", $CertPassword)
        if ($TsUrl) {
            $signArgs += @("/tr", $TsUrl, "/td", "SHA256")
        }
        $signArgs += @($f)
        & $SignTool @signArgs
        if ($LASTEXITCODE -ne 0) {
            throw "signtool sign failed with exit code $LASTEXITCODE for $f"
        }
        if ($DoVerify) {
            & $SignTool verify /pa /v $f
            if ($LASTEXITCODE -ne 0) {
                throw "signtool verify /pa failed with exit code $LASTEXITCODE for $f"
            }
        }
    }
}

$signToolPath = Find-SignTool
Write-Host "Using signtool: $signToolPath"

if ($SelfTest) {
    $tempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
    $workDir = Join-Path $tempRoot ("eb_sign_selftest_" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $workDir -Force | Out-Null

    $createdCert = $null
    $trustedThumbprint = $null
    try {
        $pwdPlain = [System.Guid]::NewGuid().ToString("N")
        $pwdSecure = ConvertTo-SecureString -String $pwdPlain -Force -AsPlainText

        $createdCert = New-SelfSignedCertificate `
            -Type CodeSigningCert `
            -Subject "CN=EnergyBaseline Phase0 SelfTest" `
            -KeyAlgorithm RSA `
            -KeyLength 2048 `
            -HashAlgorithm SHA256 `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -NotAfter (Get-Date).AddHours(2)

        $pfxFile = Join-Path $workDir "selftest.pfx"
        $cerFile = Join-Path $workDir "selftest.cer"
        Export-PfxCertificate -Cert $createdCert -FilePath $pfxFile -Password $pwdSecure | Out-Null
        Export-Certificate -Cert $createdCert -FilePath $cerFile | Out-Null

        Import-Certificate -FilePath $cerFile -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
        $trustedThumbprint = $createdCert.Thumbprint

        $pyExe = (Get-Command python).Source
        $targetExe = Join-Path $workDir "eb_smoke_target.exe"
        Copy-Item -Path $pyExe -Destination $targetExe -Force

        Invoke-SignAndVerify `
            -SignTool $signToolPath `
            -TargetFiles @($targetExe) `
            -CertPfx $pfxFile `
            -CertPassword $pwdPlain `
            -TsUrl "" `
            -DoVerify $true

        Write-Host "OK [sign_artifacts.ps1 -SelfTest] signed and verified $targetExe with SHA-256 Authenticode policy."
    }
    finally {
        if ($trustedThumbprint) {
            Remove-Item -Path ("Cert:\LocalMachine\Root\" + $trustedThumbprint) -Force -ErrorAction SilentlyContinue
        }
        if ($createdCert) {
            Remove-Item -Path $createdCert.PSPath -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path $workDir) {
            Remove-Item -Path $workDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    exit 0
}

if ($Files.Count -eq 0 -or -not $PfxPath) {
    throw "Specify -SelfTest or provide -Files and -PfxPath."
}

Invoke-SignAndVerify `
    -SignTool $signToolPath `
    -TargetFiles $Files `
    -CertPfx $PfxPath `
    -CertPassword $PfxPassword `
    -TsUrl $TimestampUrl `
    -DoVerify $Verify.IsPresent
