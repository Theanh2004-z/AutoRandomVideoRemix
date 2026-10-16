"""
Update Service Module.
Handles querying GitHub Releases API, parsing release metadata, checksums, and assets.
Designed without heavy dependencies using standard urllib.request.
"""
import json
import logging
import platform
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.version import APP_NAME, APP_VERSION, GITHUB_REPO, is_newer_version, parse_version

logger = logging.getLogger(__name__)

_SHA256_HEX_REGEX = re.compile(r"\b([a-fA-F0-9]{64})\b")


def get_current_platform_identifiers() -> Tuple[str, str]:
    """
    Returns (target_os, target_arch) for the current running environment.
    e.g. ('windows', 'x64') or ('windows', 'arm64')
    """
    target_os = "windows" if sys.platform.startswith("win") else sys.platform
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        target_arch = "x64"
    elif machine in ("arm64", "aarch64"):
        target_arch = "arm64"
    elif machine in ("i386", "i686", "x86"):
        target_arch = "x86"
    else:
        target_arch = machine
    return target_os, target_arch


def is_compatible_zip_asset(
    asset_name: str,
    app_name: str = APP_NAME,
    target_os: Optional[str] = None,
    target_arch: Optional[str] = None
) -> bool:
    """
    Strictly verifies that asset_name matches:
      1. .zip extension
      2. Application name
      3. Target OS (windows)
      4. Target Architecture (x64 vs arm64) without cross-contamination.
    Never accepts arbitrary zips or wrong architecture!
    """
    lower = asset_name.lower()
    if not lower.endswith(".zip"):
        return False

    if app_name.lower() not in lower:
        return False

    if target_os is None or target_arch is None:
        curr_os, curr_arch = get_current_platform_identifiers()
        target_os = target_os or curr_os
        target_arch = target_arch or curr_arch

    # Check OS
    if target_os == "windows":
        if "win" not in lower and "windows" not in lower:
            return False

    # Check Architecture
    if target_arch == "x64":
        has_x64 = ("x64" in lower or "x86_64" in lower or "amd64" in lower)
        has_arm = ("arm64" in lower or "aarch64" in lower or "arm" in lower)
        if not has_x64 or has_arm:
            return False
    elif target_arch == "arm64":
        has_arm = ("arm64" in lower or "aarch64" in lower)
        has_x64 = ("x64" in lower or "x86_64" in lower or "amd64" in lower)
        if not has_arm or has_x64:
            return False

    return True


@dataclass
class ReleaseAsset:
    """Represents a downloadable file attached to a release."""
    name: str
    download_url: str
    size: int = 0
    content_type: str = ""
    browser_download_url: str = ""

    def __post_init__(self):
        if not self.download_url and self.browser_download_url:
            self.download_url = self.browser_download_url


@dataclass
class ReleaseInfo:
    """Parsed release information from GitHub Releases."""
    version: str
    tag_name: str
    name: str
    body: str
    published_at: str
    html_url: str
    prerelease: bool = False
    is_newer: bool = False
    zip_asset: Optional[ReleaseAsset] = None
    manifest_asset: Optional[ReleaseAsset] = None
    sha256_asset: Optional[ReleaseAsset] = None
    sha256: Optional[str] = None
    assets: List[ReleaseAsset] = field(default_factory=list)


@dataclass
class UpdateCheckResult:
    """Result of an update check operation."""
    status: str  # "update_available" | "up_to_date" | "no_releases" | "offline" | "rate_limited" | "error"
    release_info: Optional[ReleaseInfo] = None
    error_message: Optional[str] = None

    @property
    def is_available(self) -> bool:
        return self.status == "update_available" and self.release_info is not None


def extract_sha256_from_text(text: str) -> Optional[str]:
    """Finds a 64-character SHA-256 hexadecimal hash in a string."""
    if not text:
        return None
    match = _SHA256_HEX_REGEX.search(text)
    return match.group(1).lower() if match else None


def parse_release_payload(
    payload: Dict[str, Any],
    current_version: str = APP_VERSION,
    allow_prerelease: bool = False,
    target_os: Optional[str] = None,
    target_arch: Optional[str] = None
) -> Optional[ReleaseInfo]:
    """
    Parses GitHub release JSON payload into ReleaseInfo dataclass.
    Extracts version from tag_name or name, identifies architecture-compatible ZIP and manifest assets,
    and inspects body for SHA-256 checksums.
    """
    tag_name = payload.get("tag_name", "").strip()
    if not tag_name:
        return None

    # Strip 'v' prefix
    raw_version = tag_name.lstrip("vV")
    parsed = parse_version(raw_version)
    if not parsed:
        # Try release name if tag wasn't standard
        name_cand = payload.get("name", "").strip()
        parsed = parse_version(name_cand.lstrip("vV"))
        if not parsed:
            return None
        version_str = name_cand.lstrip("vV")
    else:
        version_str = raw_version

    is_newer = is_newer_version(version_str, current_version=current_version, allow_prerelease=allow_prerelease)

    assets_raw = payload.get("assets", [])
    assets: List[ReleaseAsset] = []
    zip_asset: Optional[ReleaseAsset] = None
    manifest_asset: Optional[ReleaseAsset] = None
    sha256_asset: Optional[ReleaseAsset] = None

    curr_os, curr_arch = get_current_platform_identifiers()
    target_os = target_os or curr_os
    target_arch = target_arch or curr_arch

    for item in assets_raw:
        asset_name = item.get("name", "")
        download_url = item.get("browser_download_url", "")
        size = item.get("size", 0)
        content_type = item.get("content_type", "")
        asset = ReleaseAsset(
            name=asset_name,
            download_url=download_url,
            size=size,
            content_type=content_type
        )
        assets.append(asset)

        lower_name = asset_name.lower()
        if is_compatible_zip_asset(asset_name, app_name=APP_NAME, target_os=target_os, target_arch=target_arch):
            zip_asset = asset
        elif lower_name in ("update_manifest.json", "manifest.json"):
            manifest_asset = asset
        elif lower_name.endswith(".sha256") or lower_name.endswith(".checksum"):
            sha256_asset = asset

    body = payload.get("body", "") or ""
    sha256 = extract_sha256_from_text(body)

    return ReleaseInfo(
        version=version_str,
        tag_name=tag_name,
        name=payload.get("name") or f"{APP_NAME} v{version_str}",
        body=body,
        published_at=payload.get("published_at", ""),
        html_url=payload.get("html_url", ""),
        prerelease=bool(payload.get("prerelease", False)),
        is_newer=is_newer,
        zip_asset=zip_asset,
        manifest_asset=manifest_asset,
        sha256_asset=sha256_asset,
        sha256=sha256,
        assets=assets,
    )


def check_for_updates(
    repo: str = GITHUB_REPO,
    current_version: str = APP_VERSION,
    timeout: float = 10.0,
    api_token: Optional[str] = None,
    allow_prerelease: bool = False,
    target_os: Optional[str] = None,
    target_arch: Optional[str] = None
) -> UpdateCheckResult:
    """
    Queries GitHub Releases for the latest version.
    Returns an UpdateCheckResult indicating status and containing ReleaseInfo if available.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {
        "User-Agent": f"{APP_NAME}-Updater/{current_version}",
        "Accept": "application/vnd.github.v3+json",
    }
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return UpdateCheckResult(
                    status="error",
                    error_message=f"Máy chủ phản hồi mã lỗi {response.status}."
                )
            charset = response.headers.get_content_charset() or "utf-8"
            payload = json.loads(response.read().decode(charset))

        release_info = parse_release_payload(
            payload,
            current_version=current_version,
            allow_prerelease=allow_prerelease,
            target_os=target_os,
            target_arch=target_arch
        )
        if not release_info:
            return UpdateCheckResult(
                status="error",
                error_message="Không phân tích được thông tin phiên bản từ bản phát hành."
            )

        # If manifest_asset exists, fetch and parse it for authoritative sha256 and archive_size
        if release_info.manifest_asset:
            try:
                manifest_req = urllib.request.Request(
                    release_info.manifest_asset.download_url,
                    headers={"User-Agent": headers["User-Agent"]}
                )
                with urllib.request.urlopen(manifest_req, timeout=5.0) as m_resp:
                    m_data = json.loads(m_resp.read().decode("utf-8"))
                    if isinstance(m_data, dict):
                        manifest_sha = m_data.get("sha256", "").strip().lower()
                        if manifest_sha:
                            release_info.sha256 = manifest_sha
                        manifest_size = m_data.get("archive_size")
                        if manifest_size and release_info.zip_asset and (not release_info.zip_asset.size or release_info.zip_asset.size == 0):
                            release_info.zip_asset.size = int(manifest_size)
            except Exception as e:
                logger.debug("Failed to fetch or parse update_manifest.json: %s", e)

        # If a sha256_asset exists and sha256 is not yet found, try fetching it
        if not release_info.sha256 and release_info.sha256_asset:
            try:
                sha_req = urllib.request.Request(
                    release_info.sha256_asset.download_url,
                    headers={"User-Agent": headers["User-Agent"]}
                )
                with urllib.request.urlopen(sha_req, timeout=5.0) as sha_resp:
                    sha_text = sha_resp.read().decode("utf-8", errors="ignore")
                    extracted = extract_sha256_from_text(sha_text)
                    if extracted:
                        release_info.sha256 = extracted
            except Exception as e:
                logger.debug("Failed to fetch standalone sha256 asset: %s", e)

        status = "update_available" if release_info.is_newer else "up_to_date"
        return UpdateCheckResult(status=status, release_info=release_info)

    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            return UpdateCheckResult(
                status="rate_limited",
                error_message="Đã đạt giới hạn yêu cầu đến máy chủ GitHub. Vui lòng thử lại sau ít phút hoặc tải bản mới trực tiếp từ GitHub."
            )
        elif e.code == 404:
            # Endpoint or release not found
            return UpdateCheckResult(
                status="no_releases",
                error_message="Chưa có bản phát hành nào hoặc không tìm thấy đường dẫn phát hành trên GitHub (HTTP 404)."
            )
        else:
            return UpdateCheckResult(
                status="error",
                error_message=f"Lỗi kết nối GitHub (HTTP {e.code}): {e.reason}"
            )
    except urllib.error.URLError as e:
        logger.info("Update check URLError: %s", e.reason)
        return UpdateCheckResult(
            status="offline",
            error_message="Không thể kết nối đến máy chủ cập nhật. Vui lòng kiểm tra lại kết nối mạng."
        )
    except TimeoutError:
        return UpdateCheckResult(
            status="offline",
            error_message="Quá thời gian chờ khi kết nối tới máy chủ cập nhật."
        )
    except Exception as e:
        logger.exception("Unexpected error during update check: %s", e)
        return UpdateCheckResult(
            status="error",
            error_message=f"Đã xảy ra lỗi không xác định: {str(e)}"
        )
