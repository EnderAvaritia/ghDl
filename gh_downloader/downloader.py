"""
Download orchestration for gh-downloader.

Manages concurrent downloads of release assets with progress tracking
and resume support.
"""

from pathlib import Path


def download_asset(url: str, dest: Path) -> Path:
    """Download a single asset from *url* to *dest*.

    Returns the path to the downloaded file.
    """
    return dest


def download_release(owner: str, repo: str, tag: str, dest: Path) -> list[Path]:
    """Download all assets for a specific release tag."""
    return []
