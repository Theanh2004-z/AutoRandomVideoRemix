"""
Hardware & GPU Detection Module.
Automatically detects dedicated GPUs (NVIDIA, AMD, Intel Arc/QuickSync),
validates hardware encoding capabilities via FFmpeg probe,
and selects the optimal video encoder for any computer system.
"""
import os
import sys
import subprocess
import json
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple


def _ensure_bin_path():
    """Ensure bundled or local bin/ directory containing ffmpeg/ffprobe is prepended to PATH."""
    search_dirs = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"),
    ]
    if getattr(sys, 'frozen', False):
        search_dirs.insert(0, os.path.join(os.path.dirname(sys.executable), "bin"))
        if hasattr(sys, '_MEIPASS'):
            search_dirs.insert(0, os.path.join(sys._MEIPASS, "bin"))

    current_path = os.environ.get("PATH", "")
    for d in search_dirs:
        if d and os.path.isdir(d) and d not in current_path:
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            current_path = os.environ["PATH"]


_ensure_bin_path()


@dataclass
class GPUInfo:
    name: str
    vram_mb: int = 0
    driver_version: str = ""
    is_dedicated: bool = False
    vendor: str = "Unknown"  # "NVIDIA", "AMD", "Intel", "Other"


@dataclass
class HardwareProfile:
    active_encoder: str              # "h264_nvenc" | "h264_amf" | "h264_qsv" | "libx264"
    encoder_name_display: str        # e.g. "NVIDIA NVENC (Siêu tốc)"
    primary_gpu_name: str            # e.g. "NVIDIA GeForce GTX 1050 Ti (4GB VRAM)"
    has_dedicated_gpu: bool          # True | False
    detected_gpus: List[GPUInfo]
    supported_hardware_encoders: List[str]  # e.g. ["h264_nvenc", "h264_qsv"]


# Cached profile to avoid probing multiple times per app session
_CACHED_PROFILE: Optional[HardwareProfile] = None


def _probe_single_encoder(encoder_name: str) -> bool:
    """
    Runs a tiny, instant FFmpeg render test (1 frame, 64x64, 0.05s) to guarantee
    the hardware encoder is operational on this specific machine and driver.
    """
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=0.05:size=64x64:rate=30",
        "-c:v", encoder_name,
        "-f", "null", "-"
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        return proc.returncode == 0
    except Exception:
        return False


def get_system_gpus() -> List[GPUInfo]:
    """
    Queries installed GPUs on Windows via PowerShell / WMI.
    """
    gpus: List[GPUInfo] = []
    if os.name != 'nt':
        return gpus

    # Query WMI Win32_VideoController using PowerShell JSON output
    ps_cmd = (
        'Get-CimInstance Win32_VideoController | '
        'Select-Object Name, AdapterRAM, DriverVersion | '
        'ConvertTo-Json -Compress'
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if res.returncode == 0 and res.stdout.strip():
            raw = json.loads(res.stdout.strip())
            items = raw if isinstance(raw, list) else [raw]
            for it in items:
                name = it.get("Name", "").strip()
                if not name:
                    continue
                ram_bytes = it.get("AdapterRAM", 0) or 0
                vram_mb = int(ram_bytes // (1024 * 1024)) if isinstance(ram_bytes, (int, float)) else 0
                driver = it.get("DriverVersion", "") or ""

                name_lower = name.lower()
                is_dedicated = False
                vendor = "Other"

                if "nvidia" in name_lower or "geforce" in name_lower or "quadro" in name_lower:
                    vendor = "NVIDIA"
                    is_dedicated = True
                elif "amd" in name_lower or "radeon" in name_lower:
                    vendor = "AMD"
                    # Integrated Radeon usually shares system RAM, dedicated typically > 2GB
                    is_dedicated = bool(vram_mb >= 2048 or "rx" in name_lower or "pro" in name_lower)
                elif "intel" in name_lower:
                    vendor = "Intel"
                    is_dedicated = bool("arc" in name_lower)

                gpus.append(GPUInfo(
                    name=name,
                    vram_mb=vram_mb,
                    driver_version=driver,
                    is_dedicated=is_dedicated,
                    vendor=vendor
                ))
    except Exception:
        pass

    return gpus


def detect_hardware(force_refresh: bool = False) -> HardwareProfile:
    """
    Detects all GPUs, tests hardware encoders with live FFmpeg probes,
    and returns a consolidated HardwareProfile.
    Results are cached in memory for zero-lag subsequent calls.
    """
    global _CACHED_PROFILE
    if _CACHED_PROFILE is not None and not force_refresh:
        return _CACHED_PROFILE

    gpus = get_system_gpus()

    # Sort GPUs so dedicated ones appear first
    dedicated_gpus = [g for g in gpus if g.is_dedicated]
    primary_gpu = dedicated_gpus[0] if dedicated_gpus else (gpus[0] if gpus else None)

    # Live test encoders in priority order
    supported_hw: List[str] = []

    # 1. Test NVIDIA NVENC
    if _probe_single_encoder("h264_nvenc"):
        supported_hw.append("h264_nvenc")

    # 2. Test AMD AMF
    if _probe_single_encoder("h264_amf"):
        supported_hw.append("h264_amf")

    # 3. Test Intel QuickSync (QSV)
    if _probe_single_encoder("h264_qsv"):
        supported_hw.append("h264_qsv")

    # Determine optimal active encoder
    if "h264_nvenc" in supported_hw:
        active_encoder = "h264_nvenc"
        disp_name = "NVIDIA NVENC (Tăng tốc GPU siêu tốc)"
    elif "h264_amf" in supported_hw:
        active_encoder = "h264_amf"
        disp_name = "AMD AMF (Tăng tốc GPU Radeon)"
    elif "h264_qsv" in supported_hw:
        active_encoder = "h264_qsv"
        disp_name = "Intel QSV (Tăng tốc Intel QuickSync / Arc)"
    else:
        active_encoder = "libx264"
        disp_name = "CPU đa luồng (Tương thích 100% mọi máy tính)"

    # Format primary GPU display text
    if primary_gpu:
        vram_str = f" ({primary_gpu.vram_mb // 1024}GB VRAM)" if primary_gpu.vram_mb >= 1024 else ""
        primary_gpu_str = f"{primary_gpu.name}{vram_str}"
        has_dedicated = primary_gpu.is_dedicated
    else:
        primary_gpu_str = "CPU System / Card đồ họa tiêu chuẩn"
        has_dedicated = False

    profile = HardwareProfile(
        active_encoder=active_encoder,
        encoder_name_display=disp_name,
        primary_gpu_name=primary_gpu_str,
        has_dedicated_gpu=has_dedicated,
        detected_gpus=gpus,
        supported_hardware_encoders=supported_hw
    )

    _CACHED_PROFILE = profile
    return profile
