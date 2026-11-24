#!/usr/bin/env python3
"""
Cross-platform standalone binary builder for Makerverse (#MV3D).
Compiles server.py and/or client.py (bundling mv3d.py) into standalone executables
using PyInstaller across Linux, FreeBSD, OpenBSD, macOS, and Windows.

Usage:
    python3 build.py                 # Build both server and client binaries into ./dist/
    python3 build.py --server-only   # Build only makerverse-server
    python3 build.py --client-only   # Build only makerverse-client
    python3 build.py --clean         # Remove build/, dist/, and *.spec before building
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"


def check_pyinstaller() -> list[str]:
    try:
        import PyInstaller  # noqa: F401
        return [sys.executable, "-m", "PyInstaller"]
    except ImportError:
        pyi_bin = shutil.which("pyinstaller")
        if pyi_bin:
            return [pyi_bin]
    print("[!] PyInstaller is not installed in the active Python environment.")
    print("    Install it with:  pip install pyinstaller")
    sys.exit(1)


def clean_artifacts() -> None:
    for d in (DIST_DIR, BUILD_DIR):
        if d.exists():
            print(f"[*] Removing {d} ...")
            shutil.rmtree(d, ignore_errors=True)
    for spec in ROOT.glob("*.spec"):
        try:
            spec.unlink()
        except OSError:
            pass


def build_target(
    pyi_cmd: list[str],
    entry_script: str,
    output_name: str,
    hidden_imports: list[str],
    collect_all: list[str] | None = None,
    windowed: bool = False,
) -> Path:
    entry_path = ROOT / entry_script
    if not entry_path.is_file():
        raise FileNotFoundError(f"Entry script not found: {entry_path}")

    cmd = [
        *pyi_cmd,
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name", output_name,
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR / output_name),
        "--specpath", str(BUILD_DIR),
        "--paths", str(ROOT),
    ]
    if windowed:
        cmd.append("--windowed")

    for pkg in (collect_all or []):
        cmd.extend(["--collect-all", pkg])

    for mod in hidden_imports:
        cmd.extend(["--hidden-import", mod])

    cmd.append(str(entry_path))

    print(f"\n[*] Building {output_name} from {entry_script} ({platform.system()} / {platform.machine()})...")
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"PyInstaller failed for {output_name} (exit code {res.returncode})")

    ext = ".exe" if os.name == "nt" else ""
    out_bin = DIST_DIR / f"{output_name}{ext}"
    if not out_bin.is_file():
        raise FileNotFoundError(f"Expected output binary missing: {out_bin}")

    if os.name != "nt":
        try:
            os.chmod(out_bin, 0o755)
        except OSError:
            pass

    size_mb = out_bin.stat().st_size / (1024 * 1024)
    print(f"[+] Built {out_bin} ({size_mb:.2f} MB)")
    return out_bin


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile Makerverse (#MV3D) into standalone binaries.")
    parser.add_argument("--server-only", action="store_true", help="Compile only the headless server binary")
    parser.add_argument("--client-only", action="store_true", help="Compile only the 3D desktop client binary")
    parser.add_argument("--clean", action="store_true", help="Clean previous build/ and dist/ directories first")
    args = parser.parse_args()

    if args.server_only and args.client_only:
        parser.error("Cannot specify both --server-only and --client-only.")

    if args.clean:
        clean_artifacts()

    pyi_cmd = check_pyinstaller()
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    built: list[Path] = []
    if not args.client_only:
        built.append(
            build_target(
                pyi_cmd,
                entry_script="server.py",
                output_name="makerverse-server",
                hidden_imports=[
                    "mv3d",
                    "websockets",
                    "cffi",
                    "_cffi_backend",
                    "nacl",
                    "nacl.signing",
                    "nacl.exceptions",
                ],
                collect_all=["nacl", "cffi"],
                windowed=False,
            )
        )

    if not args.server_only:
        built.append(
            build_target(
                pyi_cmd,
                entry_script="client.py",
                output_name="makerverse-client",
                hidden_imports=[
                    "mv3d",
                    "pygame",
                    "OpenGL",
                    "OpenGL.GL",
                    "OpenGL.GL.shaders",
                    "OpenGL.platform.glx",
                    "OpenGL.platform.win32",
                    "OpenGL.platform.darwin",
                    "numpy",
                    "websockets",
                    "cffi",
                    "_cffi_backend",
                    "nacl",
                    "nacl.signing",
                    "nacl.exceptions",
                ],
                collect_all=["nacl", "cffi"],
                windowed=(os.name == "nt"),
            )
        )

    print("\n[PASS] Standalone build complete:")
    for b in built:
        print(f"  - {b}")


if __name__ == "__main__":
    main()
