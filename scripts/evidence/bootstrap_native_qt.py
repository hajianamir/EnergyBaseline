"""Rebuild the isolated Linux Qt test runtime when distro packages are unavailable.

Prefer distro libgl1/libegl1/libxkbcommon0/libdbus-1-3 for normal CI/deployment.
This fallback uses genuine native libraries, never mocked Qt objects. The old
DBus build is a test-only client dependency, not a deployed bus service.
Requires Linux x86-64, Python 3.11, cc, ld, and system X11/Xext runtime libraries.
Downloaded inputs are verified against recorded SHA-256 and Git commit IDs.
All tools, sources and binaries stay under the ignored .cache directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / ".cache/native-rebuild")
    args = parser.parse_args()
    work = args.runtime.resolve()
    if not work.is_relative_to(ROOT / ".cache"):
        parser.error("Runtime must be inside the repository .cache directory.")
    work.mkdir(parents=True, exist_ok=True)
    prefix = work / "prefix"
    lib = prefix / "lib"
    include = prefix / "include"
    lib.mkdir(parents=True, exist_ok=True)
    include.mkdir(parents=True, exist_ok=True)
    archives = work / "archives"
    archives.mkdir(exist_ok=True)
    env = os.environ.copy()

    def run(*command: str) -> None:
        print("+", *map(str, command), flush=True)
        subprocess.run(list(map(str, command)), cwd=work, env=env, check=True)

    tools = work / "toolenv"
    if not (tools / "bin/python").exists():
        run(sys.executable, "-m", "venv", str(tools))
    run(
        str(tools / "bin/python"),
        "-m",
        "pip",
        "install",
        "meson==1.9.1",
        "ninja==1.13.2",
        "cmake==4.4.5",
        "mako==1.4.3",
        "pkgconf==3.0.7.post0",
    )
    env.update(
        PATH=f"{tools / 'bin'}:{env['PATH']}",
        PKG_CONFIG_PATH=str(lib / "pkgconfig"),
        LD_LIBRARY_PATH=f"{lib}:{env.get('LD_LIBRARY_PATH', '')}",
    )
    manifest = json.loads((ROOT / "artifacts/remediation/native/native-runtime-sources.json").read_text())
    sources = {}
    for item in manifest["sources"]:
        archive = archives / item["file"]
        if not archive.exists():
            if "url" in item:
                run("curl", "-fLsS", item["url"], "-o", str(archive))
            else:
                run(
                    str(tools / "bin/python"),
                    "-m",
                    "pip",
                    "download",
                    "--only-binary=:all:",
                    "--no-deps",
                    "--dest",
                    str(archives),
                    item["pypi"],
                )
        if hashlib.sha256(archive.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Source checksum mismatch: {archive.name}")
        if "url" in item:
            with tarfile.open(archive) as tf:
                if tf.pax_headers.get("comment") != item["commit"]:
                    raise RuntimeError(f"Source Git commit mismatch: {archive.name}")
                top = tf.getnames()[0].split("/")[0]
                if not (work / top).exists():
                    # Python 3.11.2 lacks tarfile's newer data filter. Verify
                    # every path/link before extracting the hash-pinned input.
                    for member in tf.getmembers():
                        destination = (work / member.name).resolve()
                        if not destination.is_relative_to(work) or member.isdev():
                            raise RuntimeError("Unsafe source archive member.")
                        if member.issym() or member.islnk():
                            base = destination.parent if member.issym() else work
                            if not (base / member.linkname).resolve().is_relative_to(work):
                                raise RuntimeError("Unsafe source archive link.")
                    tf.extractall(work)
                sources[item["file"]] = work / top
        else:
            with zipfile.ZipFile(archive) as wheel:
                (lib / Path(item["member"]).name).write_bytes(wheel.read(item["member"]))
            (lib / "libxkbcommon.so.0").unlink(missing_ok=True)
            (lib / "libxkbcommon.so.0").symlink_to(Path(item["member"]).name)

    def cmake(source: Path, name: str, *options: str) -> None:
        build = work / f"{name}-build"
        run(
            "cmake",
            "-S",
            str(source),
            "-B",
            str(build),
            "-G",
            "Ninja",
            f"-DCMAKE_INSTALL_PREFIX={prefix}",
            f"-DCMAKE_PREFIX_PATH={prefix}",
            *options,
        )
        run("cmake", "--build", str(build), "-j2")
        run("cmake", "--install", str(build))

    cmake(
        sources["expat-src.tar.gz"] / "expat",
        "expat",
        "-DEXPAT_BUILD_DOCS=OFF",
        "-DEXPAT_BUILD_EXAMPLES=OFF",
        "-DEXPAT_BUILD_TESTS=OFF",
        "-DEXPAT_BUILD_TOOLS=OFF",
        "-DEXPAT_SHARED_LIBS=ON",
    )
    cmake(
        sources["dbus-src.tar.gz"],
        "dbus",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
        "-DDBUS_BUILD_TESTS=OFF",
        "-DDBUS_WITH_GLIB=OFF",
    )
    shutil.copytree(sources["x11-src.tar.gz"] / "include/X11", include / "X11", dirs_exist_ok=True)
    shutil.copytree(sources["xorgproto-pinned.tar.gz"] / "include", include, dirs_exist_ok=True)
    template = include / "X11/XlibConf.h.in"
    (include / "X11/XlibConf.h").write_text(
        template.read_text()
        .replace("#undef XTHREADS", "#define XTHREADS 1")
        .replace("#undef XUSE_MTSAFE_API", "#define XUSE_MTSAFE_API 1")
    )
    pc = lib / "pkgconfig"
    pc.mkdir(exist_ok=True)
    for name, version, linkage, soname in [
        ("x11", "1.8.4", "-lX11", "libX11.so.6"),
        ("xext", "1.3.4", "-lXext", "libXext.so.6"),
        ("glproto", "1.4.17", "", ""),
    ]:
        if soname:
            system = Path("/usr/lib/x86_64-linux-gnu") / soname
            if not system.is_file():
                raise RuntimeError(f"Required genuine system runtime is missing: {system}")
            link = lib / soname.split(".so")[0]
            link = link.with_name(link.name + ".so")
            link.unlink(missing_ok=True)
            link.symlink_to(system)
        (pc / f"{name}.pc").write_text(
            f"prefix={prefix}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include\nName: {name}\n"
            f"Description: Source headers and actual system native libraries\nVersion: {version}\n"
            f"Libs: -L${{libdir}} {linkage}\nCflags: -I${{includedir}}\n"
        )
    build = work / "gl-build"
    if not (build / "build.ninja").exists():
        run(
            "meson",
            "setup",
            str(build),
            str(sources["libglvnd-src.tar.gz"]),
            f"--prefix={prefix}",
            "--libdir=lib",
            "-Dx11=enabled",
            "-Dglx=enabled",
        )
    run("meson", "compile", "-C", str(build), "-j2")
    run("meson", "install", "-C", str(build))
    env["QT_QPA_PLATFORM"] = "offscreen"
    run(
        str(ROOT / ".venv/bin/python"),
        "-c",
        "from PySide6.QtWidgets import QApplication; a=QApplication([]); print(a.platformName())",
    )
    print(f"Native runtime ready. Set LD_LIBRARY_PATH={lib} and QT_QPA_PLATFORM=offscreen.")


if __name__ == "__main__":
    main()
