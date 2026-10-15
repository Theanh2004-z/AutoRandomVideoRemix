"""
Filename Generator module for AutoRandomVideoRemix.

Implements daily date-based output naming scheme:
- 1st file on date: MMDD.ext (e.g., 1014.mp4, 1014.mp3)
  where MM is 2-digit month and DD is 2-digit day.
- 2nd file: MMDD(0).ext
- 3rd file: MMDD(1).ext
- (k)-th file: MMDD(k-2).ext
Applies seamlessly to both video (.mp4) and audio (.mp3, .wav, .m4a) exports.
"""
import os
from datetime import datetime
from typing import Optional, Set


def get_daily_base_name(now: Optional[datetime] = None) -> str:
    """
    Returns 4-digit date string MMDD (e.g. '1014' for Oct 14).
    Month is 2 digits (01-12), Day is 2 digits (01-31).
    """
    if now is None:
        now = datetime.now()
    return now.strftime("%m%d")


def resolve_base_prefix(prefix: Optional[str] = None, now: Optional[datetime] = None) -> str:
    """
    Resolves the base name.
    If prefix is empty, None, 'auto', '{date}', or 'Random_Output', defaults to daily MMDD.
    Otherwise uses the specified prefix string.
    """
    if not prefix or prefix.strip() in ("", "Random_Output", "auto", "{date}"):
        return get_daily_base_name(now)
    return prefix.strip()


def get_next_output_path(
    output_dir: str,
    ext: str,
    prefix: Optional[str] = None,
    reserved_paths: Optional[Set[str]] = None,
    now: Optional[datetime] = None
) -> str:
    """
    Generates the next available output file path in output_dir.
    Naming sequence:
      1. {base}.{ext}           (e.g., 1014.mp4 / 1014.mp3)
      2. {base}(0).{ext}        (e.g., 1014(0).mp4 / 1014(0).mp3)
      3. {base}(1).{ext}
      4. {base}(2).{ext}
      ...
    Checks both filesystem existence and reserved_paths set.
    Automatically adds the selected candidate to reserved_paths if provided.
    """
    clean_ext = ext.lstrip(".").lower()
    base = resolve_base_prefix(prefix, now)

    # 1. Primary candidate: {base}.{ext}
    primary_candidate = os.path.join(output_dir, f"{base}.{clean_ext}")
    is_taken = os.path.exists(primary_candidate) or (
        reserved_paths is not None and primary_candidate in reserved_paths
    )
    if not is_taken:
        if reserved_paths is not None:
            reserved_paths.add(primary_candidate)
        return primary_candidate

    # 2. Sequential candidates: {base}(0).{ext}, {base}(1).{ext}, ...
    idx = 0
    while True:
        candidate = os.path.join(output_dir, f"{base}({idx}).{clean_ext}")
        is_taken = os.path.exists(candidate) or (
            reserved_paths is not None and candidate in reserved_paths
        )
        if not is_taken:
            if reserved_paths is not None:
                reserved_paths.add(candidate)
            return candidate
        idx += 1
