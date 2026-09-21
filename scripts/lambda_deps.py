#!/usr/bin/env python3
"""Build the shared dependency layer for NightShift's Lambda functions.

Two subcommands:

    lock    resolve requirements/lambda-deps.in for the Lambda runtime's
            platform and write lambda-deps.lock with a sha256 per wheel
    build   install the locked wheels and write a deterministic layer zip

Why a lock file with hashes. Pinning versions alone still lets a rebuilt or
replaced wheel change what ships. Hashes make the build fail instead. They
also give determinism for free: identical wheels in, identical bytes out, so
a plan from a laptop and a plan from a CI runner agree. M1 taught that lesson
the hard way, when a stray __pycache__ made the same commit produce two
different artifacts.

Why the zip is written by hand rather than with shutil.make_archive. A zip
records a modification time and a mode for every entry. Those come from the
filesystem and differ between machines. Every entry here gets a fixed 1980
timestamp and mode 0644, and entries are written in sorted order.

Mode 0644 is right for the bundled .so files too. dlopen needs the file
readable, not executable, which is why system shared libraries are 0644.

Two digests are reported. The content digest covers file names and contents
only, so it is independent of how the archive is written, and answers "did we
install the same thing?". The zip digest is what Terraform hashes, and answers
"will this deploy?". When two machines disagree, the first digest says whether
to look at packaging or at archiving. That distinction earned its place: a
runner and this laptop once produced zips with different hashes and identical
sizes, which looked exactly like a zlib difference and was not. The content
digest showed the installed files differed, and build/layer-manifest.txt
named the single file responsible.

Compression is on. Once the real cause was fixed, deflate reproduced byte for
byte across machines, and the 20 MiB it saves is headroom against the 50 MiB
upload limit that later dependencies will need.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_IN = REPO_ROOT / "requirements" / "lambda-deps.in"
LOCK_FILE = REPO_ROOT / "requirements" / "lambda-deps.lock"
BUILD_DIR = REPO_ROOT / "build"
LAYER_ROOT = BUILD_DIR / "layer"
LAYER_ZIP = BUILD_DIR / "nightshift-deps-layer.zip"
MANIFEST = BUILD_DIR / "layer-manifest.txt"

# Must match the runtime and architecture in terraform/modules/lambda_service.
PYTHON_VERSION = "3.14"
IMPLEMENTATION = "cp"

# Amazon Linux 2023 ships glibc 2.34, so it satisfies both of these. psycopg
# publishes manylinux_2_28 wheels; older packages often publish manylinux2014.
# pip picks the best match from whichever tags it is allowed to consider.
PLATFORMS = ["manylinux_2_28_aarch64", "manylinux2014_aarch64"]

# A Python layer must put importable packages under python/ at the zip root.
LAYER_PYTHON_DIR = "python"

# Fixed across every build. Any real timestamp would make the zip differ
# between machines, which is exactly the bug this script exists to avoid.
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FIXED_FILE_MODE = 0o644


def pip(*args: str) -> None:
    """Run pip from the interpreter running this script."""
    subprocess.run([sys.executable, "-m", "pip", *args], check=True)


def require_pip() -> None:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(
            f"{sys.executable} has no pip.\n"
            "Create a virtual environment first:\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/python scripts/lambda_deps.py build"
        )


def platform_args() -> list[str]:
    args = [
        "--only-binary=:all:",
        "--python-version",
        PYTHON_VERSION,
        "--implementation",
        IMPLEMENTATION,
    ]
    for plat in PLATFORMS:
        args += ["--platform", plat]
    return args


def cmd_lock() -> None:
    """Resolve the .in file for the Lambda platform and write the lock file."""
    require_pip()
    download_dir = BUILD_DIR / "wheels"
    if download_dir.exists():
        shutil.rmtree(download_dir)
    download_dir.mkdir(parents=True)

    print(
        f"Resolving {REQUIREMENTS_IN.name} for {IMPLEMENTATION}{PYTHON_VERSION.replace('.', '')} aarch64"
    )
    pip(
        "download",
        "-r",
        str(REQUIREMENTS_IN),
        "--dest",
        str(download_dir),
        *platform_args(),
    )

    entries: dict[str, tuple[str, str]] = {}
    for wheel in sorted(download_dir.glob("*.whl")):
        name, version = wheel.name.split("-")[0], wheel.name.split("-")[1]
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        entries[name.replace("_", "-").lower()] = (version, digest)

    lines = [
        "# Generated by scripts/lambda_deps.py lock. Do not edit by hand.",
        "#",
        f"# Target: Python {PYTHON_VERSION}, aarch64, {' / '.join(PLATFORMS)}",
        "# Every wheel is pinned by version and sha256. A build fails if a",
        "# wheel's bytes ever change, which is the point.",
        "#",
        "# Regenerate with:  .venv/bin/python scripts/lambda_deps.py lock",
        "",
    ]
    for name in sorted(entries):
        version, digest = entries[name]
        lines.append(f"{name}=={version} \\")
        lines.append(f"    --hash=sha256:{digest}")
    LOCK_FILE.write_text("\n".join(lines) + "\n")

    print(
        f"\nWrote {LOCK_FILE.relative_to(REPO_ROOT)} with {len(entries)} pinned wheels:"
    )
    for name in sorted(entries):
        print(f"  {name}=={entries[name][0]}")


def prune(root: Path) -> int:
    """Remove build noise that would otherwise ship to production."""
    removed = 0
    for pycache in root.rglob("__pycache__"):
        shutil.rmtree(pycache, ignore_errors=True)
        removed += 1
    for compiled in list(root.rglob("*.pyc")) + list(root.rglob("*.pyo")):
        compiled.unlink(missing_ok=True)
        removed += 1
    # pip --target drops console scripts in bin/. Nothing in a Lambda runs
    # them, and shipping an executable we never call is free risk.
    console_scripts = root / "bin"
    if console_scripts.is_dir():
        shutil.rmtree(console_scripts)
        removed += 1
    removed += drop_escaping_record_entries(root)
    return removed


def drop_escaping_record_entries(root: Path) -> int:
    """Remove RECORD lines that point outside the layer.

    A wheel's RECORD lists every installed file with its hash. pip generates
    console scripts itself and writes them with a shebang naming the
    interpreter that did the install, so the same wheel yields a different
    script on every machine. Those scripts land outside the import tree and
    are pruned above, but RECORD still carries their hash, and that alone was
    enough to make a runner's layer differ from a laptop's byte for byte.

    Dropping entries that escape the layer is not a workaround. RECORD is
    meant to describe what is installed, and a file that is not in the
    artifact does not belong in it.
    """
    dropped = 0
    for record in root.rglob("*.dist-info/RECORD"):
        lines = record.read_text().splitlines()
        kept = [line for line in lines if not line.split(",", 1)[0].startswith("../")]
        if len(kept) != len(lines):
            # Rewrite line by line rather than through a csv writer, so every
            # surviving line keeps its exact original bytes.
            record.write_text("\n".join(kept) + "\n")
            dropped += len(lines) - len(kept)
    return dropped


def sorted_files(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    )


def write_manifest(source_root: Path, out_path: Path) -> None:
    """One line per file: sha256 and path, sorted.

    Diffing two of these says exactly which files differ between machines,
    which a single rolled-up digest cannot.
    """
    lines = [
        f"{hashlib.sha256((source_root / rel).read_bytes()).hexdigest()}  {rel}"
        for rel in sorted_files(source_root)
    ]
    out_path.write_text("\n".join(lines) + "\n")


def content_digest(source_root: Path) -> str:
    """Hash names and contents only, ignoring how they get archived.

    If this matches between two machines but the zip digest does not, the
    installed files are identical and the archiver is the problem.
    """
    digest = hashlib.sha256()
    for relative in sorted_files(source_root):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256((source_root / relative).read_bytes()).digest())
    return digest.hexdigest()


def write_deterministic_zip(source_root: Path, out_path: Path) -> str:
    """Zip source_root so the bytes depend only on the file contents."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative in sorted_files(source_root):
            info = zipfile.ZipInfo(relative, date_time=FIXED_TIMESTAMP)
            info.external_attr = FIXED_FILE_MODE << 16
            info.create_system = 3  # Unix, so the mode above is honoured
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (source_root / relative).read_bytes())
    return hashlib.sha256(out_path.read_bytes()).hexdigest()


def cmd_build() -> None:
    """Install the locked wheels and write the layer zip."""
    require_pip()
    if not LOCK_FILE.exists():
        sys.exit(
            f"{LOCK_FILE} is missing. Run: {sys.executable} scripts/lambda_deps.py lock"
        )

    if LAYER_ROOT.exists():
        shutil.rmtree(LAYER_ROOT)
    target = LAYER_ROOT / LAYER_PYTHON_DIR
    target.mkdir(parents=True)

    print(f"Installing from {LOCK_FILE.name} into {target.relative_to(REPO_ROOT)}")
    pip(
        "install",
        "-r",
        str(LOCK_FILE),
        "--target",
        str(target),
        "--require-hashes",
        # The lock file already contains the full transitive set. Resolving
        # again could pull something that is not pinned.
        "--no-deps",
        # No .pyc files. They embed timestamps and the runtime regenerates
        # them anyway.
        "--no-compile",
        *platform_args(),
    )

    pruned = prune(target)
    contents = content_digest(LAYER_ROOT)
    write_manifest(LAYER_ROOT, MANIFEST)
    digest = write_deterministic_zip(LAYER_ROOT, LAYER_ZIP)

    unpacked = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"\nPruned {pruned} build artefacts")
    print(f"Files:          {len(sorted_files(LAYER_ROOT))}")
    print(
        f"Layer unpacked: {unpacked / 1024 / 1024:.1f} MiB (250 MiB limit, layers plus function)"
    )
    print(
        f"Layer zipped:   {LAYER_ZIP.stat().st_size / 1024 / 1024:.1f} MiB (50 MiB limit)"
    )
    print(f"content sha256: {contents}")
    print(f"zip sha256:     {digest}")
    print(
        f"\nWrote {LAYER_ZIP.relative_to(REPO_ROOT)} and {MANIFEST.relative_to(REPO_ROOT)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("lock", help="resolve requirements and write the lock file")
    sub.add_parser("build", help="install locked wheels and write the layer zip")
    args = parser.parse_args()
    {"lock": cmd_lock, "build": cmd_build}[args.command]()


if __name__ == "__main__":
    main()
