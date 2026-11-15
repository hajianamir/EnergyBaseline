"""Build a genuine isolated SQLite ODBC test runtime when distro packages are absent.

Linux x86-64 only: requires cc, make, libc headers and the system SQLite library.
Uses checksum/commit-pinned sources, never mocked libraries. The old unixODBC
2.3.1 source is a test-only fixture; use maintained distro packages in deployment.
All binaries and configuration stay under ignored .cache; no product dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / ".cache/odbc-rebuild")
    args = parser.parse_args()
    work = args.runtime.resolve()
    if not work.is_relative_to(ROOT / ".cache"):
        parser.error("Runtime must be within the repository .cache directory.")
    work.mkdir(parents=True, exist_ok=True)
    prefix = work / "prefix"
    sources = {}
    manifest = json.loads((ROOT / "artifacts/remediation/closure/odbc-runtime-sources.json").read_text())
    for entry in manifest["sources"]:
        archive = work / entry["file"]
        if not archive.exists():
            urllib.request.urlretrieve(entry["url"], archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("ODBC source checksum mismatch.")
        with tarfile.open(archive) as tf:
            if tf.pax_headers.get("comment") != entry["commit"]:
                raise RuntimeError("ODBC source commit mismatch.")
            top = tf.getnames()[0].split("/")[0]
            sources[entry["role"]] = work / top
            if not (work / top).exists():
                for member in tf.getmembers():
                    path = (work / member.name).resolve()
                    if not path.is_relative_to(work) or member.isdev():
                        raise RuntimeError("Unsafe ODBC source archive member.")
                    if member.issym() or member.islnk():
                        base = path.parent if member.issym() else work
                        if not (base / member.linkname).resolve().is_relative_to(work):
                            raise RuntimeError("Unsafe ODBC source archive link.")
                tf.extractall(work)
    env = os.environ.copy()
    env.update(LIBRARY_PATH=str(prefix / "lib"), LD_LIBRARY_PATH=str(prefix / "lib"))

    def run(source: Path, *command: str) -> None:
        print("+", *command, flush=True)
        subprocess.run(command, cwd=source, env=env, check=True)

    manager = sources["unixodbc-azure"]
    run(manager, "sh", "configure", f"--prefix={prefix}", "--enable-drivers=no", "--enable-gui=no")
    run(manager, "make", "-j2")
    run(manager, "make", "install")
    library = Path("/usr/lib/x86_64-linux-gnu/libsqlite3.so.0")
    if not library.is_file():
        raise RuntimeError("Genuine system SQLite library is unavailable.")
    for name in ("sqlite3.h", "sqlite3ext.h"):
        shutil.copyfile(sources["sqlite-headers"] / name, prefix / "include" / name)
    link = prefix / "lib/libsqlite3.so"
    link.unlink(missing_ok=True)
    link.symlink_to(library)
    driver = sources["sqliteodbc"]
    run(driver, "sh", "configure", f"--prefix={prefix}", f"--with-odbc={prefix}", f"--with-sqlite3={prefix}")
    run(driver, "make", "-j2")
    run(driver, "make", "install")
    config = work / "config"
    config.mkdir(exist_ok=True)
    (config / "odbcinst.ini").write_text(f"[SQLite3]\nDriver={prefix / 'lib/libsqlite3odbc.so'}\n")
    print(f"Runtime ready: ODBCSYSINI={config} LD_LIBRARY_PATH={prefix / 'lib'}")
    env["ODBCSYSINI"] = str(config)
    run(
        ROOT,
        str(ROOT / ".venv/bin/python"),
        "scripts/evidence/remediation_odbc.py",
        "--out",
        str(work / "native-verification.json"),
    )


if __name__ == "__main__":
    main()
