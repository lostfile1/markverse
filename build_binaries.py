#!/usr/bin/env python3
"""
Cross-platform binary builder for Makerverse (Linux & Windows) with custom icon support.

Usage:
    python build_binaries.py --target all --icon path/to/makerverse.ico
    python build_binaries.py --target client --icon path/to/icon.png
    python build_binaries.py --target server --no-onefile

Notes:
- PyInstaller builds a native binary for the host OS where it runs (run on Linux
  for a Linux binary; run on Windows for a Windows .exe).
- For Windows builds, pass a .ico file (or a .png if Pillow is installed so it
  can convert it to a multi-size .ico automatically).
- For Linux builds, PyInstaller packages the ELF binary, and this script also
  generates a .desktop launcher + copies a PNG icon next to the dist output so
  desktop environments display your custom icon.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _resolve_icon(icon_arg: str | None, out_dir: Path) -> tuple[str | None, Path | None]:
    """
    Returns (pyinstaller_icon_path, desktop_png_path).
    On Windows, PyInstaller expects .ico; if given a .png and Pillow is available,
    generates a temporary/build .ico. On Linux, keeps/copies a .png for .desktop entry.
    """
    if not icon_arg:
        return None, None

    src = Path(icon_arg).expanduser().resolve()
    if not src.is_file():
        print(f"[WARN] Icon file not found: {src} — building without icon.")
        return None, None

    sys_name = platform.system().lower()
    build_assets = out_dir / "_icon_assets"
    build_assets.mkdir(parents=True, exist_ok=True)

    if sys_name == "windows":
        if src.suffix.lower() == ".ico":
            return str(src), None
        if src.suffix.lower() == ".png":
            try:
                from PIL import Image  # type: ignore

                ico_path = build_assets / f"{src.stem}.ico"
                img = Image.open(src).convert("RGBA")
                img.save(
                    ico_path,
                    format="ICO",
                    sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
                )
                print(f"[*] Converted {src.name} -> {ico_path}")
                return str(ico_path), None
            except Exception as exc:
                print(f"[WARN] Could not convert {src.name} to .ico ({exc}). Pass a .ico on Windows.")
                return None, None
        return str(src), None

    # Linux / macOS
    png_out = None
    if src.suffix.lower() == ".png":
        png_out = build_assets / f"{src.stem}.png"
        shutil.copy2(src, png_out)
    elif src.suffix.lower() == ".ico":
        try:
            from PIL import Image  # type: ignore

            png_out = build_assets / f"{src.stem}.png"
            Image.open(src).convert("RGBA").save(png_out, format="PNG")
        except Exception:
            png_out = None

    return (str(png_out) if png_out else str(src)), png_out


def _write_linux_desktop_entry(dist_dir: Path, exe_name: str, png_icon: Path | None, windowed: bool) -> None:
    if platform.system().lower() != "linux":
        return
    exe_path = dist_dir / exe_name
    if not exe_path.exists():
        return

    icon_field = "application-x-executable"
    if png_icon and png_icon.exists():
        installed_icon = dist_dir / png_icon.name
        if installed_icon != png_icon:
            shutil.copy2(png_icon, installed_icon)
        icon_field = str(installed_icon)

    desktop_file = dist_dir / f"{exe_name}.desktop"
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={exe_name}\n"
        f"Exec=\"{exe_path}\"\n"
        f"Icon={icon_field}\n"
        f"Terminal={'false' if windowed else 'true'}\n"
        "Categories=Game;Network;\n"
    )
    desktop_file.write_text(content, encoding="utf-8")
    try:
        os.chmod(desktop_file, 0o755)
    except OSError:
        pass
    print(f"[*] Wrote Linux desktop launcher: {desktop_file}")


def _build_one(
    entry_script: Path,
    mv3d_module: Path,
    exe_name: str,
    dist_dir: Path,
    work_dir: Path,
    spec_dir: Path,
    onefile: bool,
    windowed: bool,
    icon_path: str | None,
    extra_hidden_imports: list[str],
) -> int:
    if not entry_script.is_file():
        print(f"[ERR] Missing entry script: {entry_script}")
        return 1

    sep = ";" if platform.system().lower() == "windows" else ":"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        f"--name={exe_name}",
        f"--distpath={dist_dir}",
        f"--workpath={work_dir}",
        f"--specpath={spec_dir}",
        f"--paths={mv3d_module.parent}",
        "--add-data",
        f"{mv3d_module}{sep}mv3d.py",
    ]

    cmd.extend(["--collect-all", "nacl", "--collect-all", "cffi"])
    cmd.append("--onefile" if onefile else "--onedir")
    if windowed and platform.system().lower() in ("windows", "darwin"):
        cmd.append("--windowed")
    else:
        cmd.append("--console")

    if icon_path:
        cmd.append(f"--icon={icon_path}")

    for mod in extra_hidden_imports:
        cmd.extend(["--hidden-import", mod])

    cmd.append(str(entry_script))

    print("[*] Running:", " ".join(cmd))
    proc = subprocess.run(cmd)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Linux/Windows binaries for Makerverse with custom icon.")
    parser.add_argument(
        "--target",
        choices=("client", "server", "all"),
        default="all",
        help="Which Makerverse binary to build (default: all)",
    )
    parser.add_argument(
        "--icon",
        default=None,
        help="Path to custom icon (.ico for Windows, .png for Linux; auto-converts if Pillow is installed)",
    )
    parser.add_argument(
        "--no-onefile",
        action="store_true",
        help="Build as a directory bundle (--onedir) instead of a single executable (--onefile)",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Keep console window visible for the client binary (useful for debugging)",
    )
    args = parser.parse_args()

    if shutil.which("pyinstaller") is None:
        try:
            subprocess.run([sys.executable, "-m", "PyInstaller", "--version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            print("[ERR] PyInstaller is not installed. Install it with: pip install pyinstaller")
            return 1

    root = _repo_root()
    pkg_dir = root / "makerverse" if (root / "makerverse" / "client.py").is_file() else root
    client_py = pkg_dir / "client.py"
    server_py = pkg_dir / "server.py"
    mv3d_py = pkg_dir / "mv3d.py"

    os_tag = platform.system().lower()
    arch_tag = platform.machine().lower() or "x64"
    dist_dir = root / "dist" / f"{os_tag}-{arch_tag}"
    work_dir = root / "build" / "pyinstaller_work"
    spec_dir = root / "build" / "pyinstaller_spec"

    pyi_icon, desktop_png = _resolve_icon(args.icon, root / "build")
    onefile = not args.no_onefile
    ext = ".exe" if os_tag == "windows" else ""

    client_hidden = [
        "mv3d",
        "numpy",
        "pygame",
        "OpenGL",
        "OpenGL.GL",
        "OpenGL.GL.shaders",
        "websockets",
        "cffi",
        "_cffi_backend",
        "nacl",
        "nacl.signing",
        "nacl.exceptions",
    ]
    server_hidden = [
        "mv3d",
        "websockets",
        "cffi",
        "_cffi_backend",
        "nacl",
        "nacl.signing",
        "nacl.exceptions",
    ]

    rc = 0
    if args.target in ("client", "all"):
        c_name = f"makerverse-client{ext}"
        rc = _build_one(
            entry_script=client_py,
            mv3d_module=mv3d_py,
            exe_name=f"makerverse-client",
            dist_dir=dist_dir,
            work_dir=work_dir,
            spec_dir=spec_dir,
            onefile=onefile,
            windowed=not args.console,
            icon_path=pyi_icon,
            extra_hidden_imports=client_hidden,
        )
        if rc != 0:
            return rc
        _write_linux_desktop_entry(dist_dir, c_name, desktop_png, windowed=not args.console)

    if args.target in ("server", "all"):
        s_name = f"makerverse-server{ext}"
        rc = _build_one(
            entry_script=server_py,
            mv3d_module=mv3d_py,
            exe_name="makerverse-server",
            dist_dir=dist_dir,
            work_dir=work_dir,
            spec_dir=spec_dir,
            onefile=onefile,
            windowed=False,
            icon_path=pyi_icon,
            extra_hidden_imports=server_hidden,
        )
        if rc != 0:
            return rc
        _write_linux_desktop_entry(dist_dir, s_name, desktop_png, windowed=False)

    print(f"[DONE] Binaries written to: {dist_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
