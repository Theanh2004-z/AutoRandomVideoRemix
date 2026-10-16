"""
Release Packaging Automation Script.
Packages the PyInstaller output distribution directory into a production-ready ZIP archive
and generates update_manifest.json with SHA-256 checksums for GitHub Releases.
"""
import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime

# Add root directory to sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.version import APP_NAME, APP_VERSION, GITHUB_REPO, RELEASE_CHANNEL
from scripts.build_updater import ensure_updater


def calculate_sha256(filepath: str) -> str:
    """Calculates SHA-256 checksum of a file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(64 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def package_release(
    dist_dir: str,
    output_dir: str,
    version: str = APP_VERSION,
    notes: str = ""
) -> str:
    """
    Creates release archive and metadata manifest.
    """
    if not os.path.isdir(dist_dir):
        raise FileNotFoundError(f"Distribution directory not found: {dist_dir}")

    # A failed helper build must stop publication. A frozen GUI executable
    # cannot act as a Python interpreter for a copied updater script.
    ensure_updater(dist_dir)

    os.makedirs(output_dir, exist_ok=True)
    zip_filename = f"{APP_NAME}-{version}-windows-x64.zip"
    zip_filepath = os.path.join(output_dir, zip_filename)

    print(f"Creating release archive: {zip_filepath}")
    with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Walk dist_dir and add files
        for root, dirs, files in os.walk(dist_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, dist_dir)
                zf.write(full_path, rel_path)

    zip_size = os.path.getsize(zip_filepath)
    sha256_hash = calculate_sha256(zip_filepath)

    # Write .sha256 file
    sha256_file = os.path.join(output_dir, f"{zip_filename}.sha256")
    with open(sha256_file, "w", encoding="utf-8") as f:
        f.write(f"{sha256_hash}  {zip_filename}\n")

    # Generate update_manifest.json
    manifest = {
        "app_name": APP_NAME,
        "version": version,
        "release_channel": RELEASE_CHANNEL,
        "published_at": datetime.now().isoformat(),
        "executable": f"{APP_NAME}.exe",
        "archive_name": zip_filename,
        "archive_size": zip_size,
        "sha256": sha256_hash,
        "repository": GITHUB_REPO,
        "release_notes": notes or f"Release {APP_NAME} v{version}",
    }

    manifest_path = os.path.join(output_dir, "update_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print("\n--- Release Build Summary ---")
    print(f"Version:       {version}")
    print(f"Archive:       {zip_filepath} ({zip_size / (1024 * 1024):.2f} MB)")
    print(f"SHA-256:       {sha256_hash}")
    print(f"Manifest:      {manifest_path}")
    print(f"Checksum File: {sha256_file}")
    print("-----------------------------\n")
    print("To publish:")
    print(f"1. Create a GitHub Release with tag 'v{version}' on {GITHUB_REPO}")
    print(f"2. Upload '{zip_filename}' and 'update_manifest.json'")
    print(f"3. Include 'SHA256: {sha256_hash}' in the release description")
    return zip_filepath


def main():
    parser = argparse.ArgumentParser(description="Package AutoRandomVideoRemix Release")
    parser.add_argument(
        "--dist-dir",
        default=os.path.join(PROJECT_ROOT, "dist", APP_NAME),
        help="Path to built PyInstaller distribution folder"
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(PROJECT_ROOT, "releases"),
        help="Target folder for release ZIP and manifest"
    )
    parser.add_argument(
        "--version",
        default=APP_VERSION,
        help=f"Release version (defaults to {APP_VERSION})"
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Changelog or release notes description"
    )
    args = parser.parse_args()

    try:
        package_release(args.dist_dir, args.output_dir, version=args.version, notes=args.notes)
    except Exception as e:
        print(f"Error packaging release: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
