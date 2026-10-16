"""
Application Version and Release Channel Module.
Single source of truth for application version across UI, build packaging, and updater.
"""
import re
from typing import Optional, Tuple

APP_NAME = "AutoRandomVideoRemix"
APP_VERSION = "1.4.2"
RELEASE_CHANNEL = "stable"  # "stable" | "beta"
GITHUB_REPO = "Theanh2004-z/AutoRandomVideoRemix"
GITHUB_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"

_SEMVER_REGEX = re.compile(
    r"^[vV]?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)


def parse_version(version_str: str) -> Optional[Tuple[int, int, int, str]]:
    """
    Parses a version string in MAJOR.MINOR.PATCH[-PRERELEASE] format.
    Returns (major, minor, patch, prerelease_tag) or None if invalid.
    Examples:
        '1.4.2' -> (1, 4, 2, '')
        'v1.10.0' -> (1, 10, 0, '')
        'v2.0.0-beta.1' -> (2, 0, 0, 'beta.1')
    """
    if not version_str or not isinstance(version_str, str):
        return None
    match = _SEMVER_REGEX.match(version_str.strip())
    if not match:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    prerelease = match.group("prerelease") or ""
    return (major, minor, patch, prerelease)


def is_newer_version(
    candidate_version: str,
    current_version: str = APP_VERSION,
    allow_prerelease: bool = False
) -> bool:
    """
    Determines whether candidate_version is strictly newer than current_version.
    Compares numerical components (1.10.0 > 1.9.0).
    Ignores pre-releases unless allow_prerelease is True.
    """
    parsed_cand = parse_version(candidate_version)
    parsed_curr = parse_version(current_version)

    if not parsed_cand or not parsed_curr:
        return False

    cand_maj, cand_min, cand_pat, cand_pre = parsed_cand
    curr_maj, curr_min, curr_pat, curr_pre = parsed_curr

    # Ignore pre-releases in stable channel if not allowed
    if cand_pre and not allow_prerelease and RELEASE_CHANNEL == "stable":
        return False

    cand_tuple = (cand_maj, cand_min, cand_pat)
    curr_tuple = (curr_maj, curr_min, curr_pat)

    if cand_tuple > curr_tuple:
        return True
    elif cand_tuple < curr_tuple:
        return False
    else:
        # Same major.minor.patch: a stable release is newer than a prerelease
        if curr_pre and not cand_pre:
            return True
        return False
