"""Build EnergyBaseline distribution (PyInstaller onedir + .lnk shortcut + CycloneDX SBOM + per-user installer).

Usage:
    python scripts/build_exe.py                        # full profile, onedir + SBOM + .lnk
    python scripts/build_exe.py --profile lite         # lite profile (~180 MB smaller, excludes torch)
    python scripts/build_exe.py --profile lite --installer  # also compile per-user Inno Setup / NSIS installer
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_version() -> str:
    sys.path.insert(0, str(ROOT))
    import energybaseline

    return str(energybaseline.__version__)


def generate_sbom(output_path: Path) -> Path:
    """Emit a validated, reproducible CycloneDX JSON SBOM for the active build environment (PKG-6)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("SOURCE_DATE_EPOCH", "1767225600")
    cmd = [
        sys.executable,
        "-m",
        "cyclonedx_py",
        "environment",
        "--pyproject",
        str(ROOT / "pyproject.toml"),
        "--output-reproducible",
        "--of",
        "JSON",
        "-o",
        str(output_path),
        sys.executable,
    ]
    subprocess.check_call(cmd, cwd=ROOT, env=env)
    return output_path


def create_windows_shortcut(target_exe: Path, shortcut_path: Path, icon_path: Path | None = None) -> bool:
    """Create a Windows ``.lnk`` shortcut pointing to ``target_exe`` (PKG-2)."""
    if sys.platform != "win32":
        return False
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import win32com.client  # type: ignore[import-untyped]

        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(str(shortcut_path))
        sc.TargetPath = str(target_exe.resolve())
        sc.WorkingDirectory = str(target_exe.resolve().parent)
        sc.Description = "EnergyBaseline - Offline Energy Baseline Analytics"
        if icon_path and icon_path.is_file():
            sc.IconLocation = str(icon_path.resolve())
        sc.Save()
        return shortcut_path.is_file()
    except Exception:
        ico_clause = f"$s.IconLocation='{icon_path.resolve()}';" if (icon_path and icon_path.is_file()) else ""
        ps = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut_path.resolve()}');"
            f"$s.TargetPath='{target_exe.resolve()}';"
            f"$s.WorkingDirectory='{target_exe.resolve().parent}';"
            f"{ico_clause}$s.Save()"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=False,
            capture_output=True,
            text=True,
        )
        return res.returncode == 0 and shortcut_path.is_file()


def _find_iscc() -> str | None:
    found = shutil.which("ISCC") or shutil.which("ISCC.exe")
    if found:
        return found
    for cand in (
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ):
        if cand.is_file():
            return str(cand)
    return None


def _find_makensis() -> str | None:
    found = shutil.which("makensis") or shutil.which("makensis.exe")
    if found:
        return found
    for cand in (
        Path(r"C:\Program Files (x86)\NSIS\makensis.exe"),
        Path(r"C:\Program Files\NSIS\makensis.exe"),
    ):
        if cand.is_file():
            return str(cand)
    return None


def build_per_user_installer(*, dist_app_dir: Path, out_dir: Path, version: str, profile: str) -> Path:
    """Compile a per-user Windows installer (Inno Setup preferred, NSIS fallback) from ``dist_app_dir`` (PKG-2)."""
    if sys.platform != "win32":
        raise RuntimeError("Windows per-user installer compilation requires sys.platform == 'win32'.")

    work = ROOT / "build" / "installer"
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_base = f"EnergyBaseline-Setup-{profile}"
    installer_exe = out_dir / f"{setup_base}.exe"
    ico = ROOT / "assets" / "energybaseline.ico"

    iscc = _find_iscc()
    if iscc:
        iss_path = work / f"energybaseline_{profile}.iss"
        src_glob = str(dist_app_dir.resolve()) + r"\*"
        ico_line = f"SetupIconFile={ico.resolve()}" if ico.is_file() else ""
        iss = f"""[Setup]
AppId=EnergyBaseline_PerUser_v6
AppName=EnergyBaseline
AppVersion={version}
AppPublisher=EnergyBaseline
DefaultDirName={{autopf}}\\EnergyBaseline
DefaultGroupName=EnergyBaseline
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={out_dir.resolve()}
OutputBaseFilename={setup_base}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={{app}}\\EnergyBaseline.exe
{ico_line}

[Files]
Source: "{src_glob}"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{autoprograms}}\\EnergyBaseline"; Filename: "{{app}}\\EnergyBaseline.exe"; WorkingDir: "{{app}}"
Name: "{{autodesktop}}\\EnergyBaseline"; Filename: "{{app}}\\EnergyBaseline.exe"; WorkingDir: "{{app}}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
"""
        iss_path.write_text(iss, encoding="utf-8")
        subprocess.check_call([iscc, "/Q", str(iss_path)], cwd=ROOT)
        if not installer_exe.is_file():
            raise FileNotFoundError(f"Inno Setup did not emit expected installer: {installer_exe}")
        return installer_exe

    makensis = _find_makensis()
    if makensis:
        nsi_path = work / f"energybaseline_{profile}.nsi"
        nsi = f"""!include "MUI2.nsh"
Name "EnergyBaseline {version}"
OutFile "{installer_exe.resolve()}"
InstallDir "$LOCALAPPDATA\\Programs\\EnergyBaseline"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "{dist_app_dir.resolve()}\\*.*"
  CreateDirectory "$SMPROGRAMS\\EnergyBaseline"
  CreateShortCut "$SMPROGRAMS\\EnergyBaseline\\EnergyBaseline.lnk" "$INSTDIR\\EnergyBaseline.exe" "" "$INSTDIR\\EnergyBaseline.exe" 0
  WriteUninstaller "$INSTDIR\\Uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\\EnergyBaseline\\EnergyBaseline.lnk"
  RMDir "$SMPROGRAMS\\EnergyBaseline"
  RMDir /r "$INSTDIR"
SectionEnd
"""
        nsi_path.write_text(nsi, encoding="utf-8")
        subprocess.check_call([makensis, "/V2", str(nsi_path)], cwd=ROOT)
        if not installer_exe.is_file():
            raise FileNotFoundError(f"NSIS did not emit expected installer: {installer_exe}")
        return installer_exe

    raise RuntimeError("Neither Inno Setup (ISCC.exe) nor NSIS (makensis.exe) was found on PATH.")


def write_checksums(target_dir: Path) -> Path:
    """Write deterministic SHA256SUMS.txt covering top-level deliverables in ``target_dir``."""
    lines: list[str] = []
    for p in sorted(target_dir.iterdir()):
        if p.is_file() and p.name != "SHA256SUMS.txt":
            lines.append(f"{_sha256(p)}  {p.name}")
    sums_path = target_dir / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build EnergyBaseline PyInstaller onedir bundle, SBOM, and installer.")
    ap.add_argument("--profile", choices=["full", "lite"], default="full")
    ap.add_argument("--onefile", action="store_true", help="Build single-file executable instead of default onedir")
    ap.add_argument("--installer", action="store_true", help="Compile per-user Windows installer (Inno Setup / NSIS)")
    ap.add_argument("--sbom-only", action="store_true", help="Only emit CycloneDX SBOM and exit")
    ap.add_argument("--dist-dir", default=str(ROOT / "dist"), help="Output directory (default: ./dist)")
    args = ap.parse_args(argv)

    dist_root = Path(args.dist_dir).resolve()
    dist_root.mkdir(parents=True, exist_ok=True)
    version = _read_version()

    if args.sbom_only:
        sbom = generate_sbom(dist_root / "sbom.cdx.json")
        print(f"SBOM ready -> {sbom} (sha256={_sha256(sbom)})")
        return 0

    make_icon_script = ROOT / "scripts" / "make_icon.py"
    if make_icon_script.is_file():
        subprocess.check_call([sys.executable, str(make_icon_script)], cwd=ROOT)

    sep = ";" if sys.platform == "win32" else ":"
    data_pairs = [
        ("assets", "assets"),
        ("alembic", "alembic"),
        ("alembic.ini", "."),
        ("docs", "docs"),
        ("samples", "samples"),
    ]
    ico = ROOT / "assets" / "energybaseline.ico"
    layout_flag = "--onefile" if args.onefile else "--onedir"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        layout_flag,
        "--windowed",
        "--name",
        "EnergyBaseline",
        "--distpath",
        str(dist_root),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(ROOT / "build" / "pyinstaller"),
        "--paths",
        str(ROOT),
    ]
    if ico.exists():
        cmd += ["--icon", str(ico)]
    for src_rel, dst_rel in data_pairs:
        src_abs = ROOT / src_rel
        if src_abs.exists():
            cmd += ["--add-data", f"{src_abs}{sep}{dst_rel}"]

    hidden_imports = [
        "sqlalchemy.dialects.sqlite",
        "alembic.runtime.migration",
        "alembic.ddl.sqlite",
        "alembic.script.revision",
        "sklearn.utils._typedefs",
        "sklearn.neighbors._partition_nodes",
        "statsmodels.tsa.statespace._filters",
        "scipy.special._cdflib",
        "scipy._cyutility",
        "scipy.special._ufuncs_cxx",
        "pymysql",
        "psycopg2",
        "pyodbc",
        "openpyxl",
        "xlrd",
        "pyarrow",
        "pyarrow.parquet",
        "sqlglot",
        "jinja2",
        "cryptography",
        "PySide6.QtNetwork",
        "joblib",
    ]
    if sys.platform == "win32":
        hidden_imports += ["win32api", "win32con", "win32job", "pywintypes"]
    for hidden in hidden_imports:
        cmd += ["--hidden-import", hidden]

    for pkg in ("sklearn", "statsmodels"):
        cmd += ["--collect-submodules", pkg]
    for data_pkg in ("matplotlib", "sklearn"):
        cmd += ["--collect-data", data_pkg]

    if args.profile == "lite":
        cmd += ["--exclude-module", "torch", "--exclude-module", "torchvision", "--exclude-module", "torchaudio"]
    for ex in ("tkinter", "PyQt5", "PyQt6", "pytest", "IPython", "notebook"):
        cmd += ["--exclude-module", ex]
    cmd.append(str(ROOT / "energybaseline" / "launcher.py"))

    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)

    bundle_dir = dist_root if args.onefile else (dist_root / "EnergyBaseline")
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # Copy documentation and icon assets next to executable for user inspection and shortcut icons.
    for doc_rel in ("README.md", "LICENSE", "docs/USER_GUIDE.md"):
        src = ROOT / doc_rel
        if src.exists():
            shutil.copy2(src, bundle_dir / src.name)
    if ico.exists():
        (bundle_dir / "assets").mkdir(exist_ok=True)
        shutil.copy2(ico, bundle_dir / "assets" / ico.name)

    exe_name = "EnergyBaseline.exe" if sys.platform == "win32" else "EnergyBaseline"
    exe_path = bundle_dir / exe_name
    if not exe_path.exists():
        raise FileNotFoundError(f"Expected built executable was not found: {exe_path}")

    # Create .lnk shortcut inside bundle_dir on Windows (PKG-2)
    lnk_path = bundle_dir / "EnergyBaseline.lnk"
    lnk_created = create_windows_shortcut(exe_path, lnk_path, bundle_dir / "assets" / "energybaseline.ico")

    # Emit CycloneDX SBOM alongside build output (PKG-6)
    sbom_path = bundle_dir / "sbom.cdx.json"
    generate_sbom(sbom_path)
    shutil.copy2(sbom_path, dist_root / "sbom.cdx.json")

    installer_path: Path | None = None
    if args.installer:
        if args.onefile:
            raise ValueError("--installer requires default --onedir layout.")
        installer_path = build_per_user_installer(
            dist_app_dir=bundle_dir,
            out_dir=dist_root,
            version=version,
            profile=args.profile,
        )

    manifest = {
        "name": "EnergyBaseline",
        "version": version,
        "profile": args.profile,
        "layout": "onefile" if args.onefile else "onedir",
        "exe_path": str(exe_path),
        "exe_sha256": _sha256(exe_path),
        "sbom_path": str(sbom_path),
        "sbom_sha256": _sha256(sbom_path),
        "shortcut_created": lnk_created,
        "shortcut_path": str(lnk_path) if lnk_created else None,
        "installer_path": str(installer_path) if installer_path else None,
        "installer_sha256": _sha256(installer_path) if installer_path else None,
    }
    (bundle_dir / "build_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (dist_root / "build_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_checksums(bundle_dir)
    write_checksums(dist_root)

    print(f"\nBuild ready -> {exe_path}  (profile={args.profile}, layout={'onefile' if args.onefile else 'onedir'})")
    if installer_path:
        print(f"Installer ready -> {installer_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
