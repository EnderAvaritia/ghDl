"""
Tests for bandwidth parsing / pacing (``gh_downloader.utils.parse_rate`` and
``gh_downloader.utils.RateLimiter``) and their wiring into the downloader.
"""

from __future__ import annotations

import os
import threading

import pytest

from gh_downloader.downloader import DownloadManager
from gh_downloader.utils import RateLimiter, parse_rate


# ---------------------------------------------------------------------------
# parse_rate
# ---------------------------------------------------------------------------


class TestParseRate:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, 0),
            ("", 0),
            ("0", 0),
            ("off", 0),
            ("500K", 512_000),
            ("500KB", 512_000),
            ("1M", 1_048_576),
            ("1.5M", 1_572_864),
            ("10 MB/s", 10_485_760),
            ("2MiB/s", 2_097_152),
            ("1024", 1024),
            (1024, 1024),
            ("1G", 1_073_741_824),
        ],
    )
    def test_parses_supported_forms(self, value, expected):
        assert parse_rate(value) == expected

    @pytest.mark.parametrize("value", ["abc", "-1", "5X"])
    def test_rejects_invalid_values(self, value):
        with pytest.raises(ValueError):
            parse_rate(value)

    def test_suffix_is_case_insensitive(self):
        assert parse_rate("500k") == parse_rate("500K")
        assert parse_rate("1mib") == parse_rate("1MiB")


# ---------------------------------------------------------------------------
# RateLimiter pacing (fake clock, no real sleeping)
# ---------------------------------------------------------------------------


class _FakeTime:
    """Minimal ``time`` module stand-in recording sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class TestRateLimiterPacing:
    def test_sleeps_to_meet_average_rate(self, monkeypatch):
        fake_time = _FakeTime()
        monkeypatch.setattr("gh_downloader.utils.time", fake_time)

        limiter = RateLimiter(1024 * 1024)
        limiter.consume(100_000)

        # 100_000 bytes at 1 MiB/s is ~0.0954 seconds.
        assert sum(fake_time.slept) == pytest.approx(0.0954, abs=1e-3)
        assert all(sleep <= 0.2 for sleep in fake_time.slept)

    def test_unlimited_never_sleeps(self, monkeypatch):
        fake_time = _FakeTime()
        monkeypatch.setattr("gh_downloader.utils.time", fake_time)

        limiter = RateLimiter(0)
        limiter.consume(10_000_000)

        assert fake_time.slept == []
        assert limiter.bytes_per_second == 0

    def test_stop_event_returns_promptly(self, monkeypatch):
        fake_time = _FakeTime()
        monkeypatch.setattr("gh_downloader.utils.time", fake_time)

        limiter = RateLimiter(1024)
        stop_event = threading.Event()
        stop_event.set()

        limiter.consume(10_000_000, stop_event)

        assert fake_time.slept == []


class TestRateLimiterConcurrency:
    def test_shared_limiter_accounts_without_races(self):
        limiter = RateLimiter(10 ** 12)
        recorded: list[int] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(50):
                    limiter.consume(1000)
                    with limiter._lock:
                        recorded.append(limiter._consumed)
            except Exception as exc:  # pragma: no cover - failure diagnostic
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert limiter._consumed == 100_000
        assert recorded == sorted(recorded)


# ---------------------------------------------------------------------------
# Downloader wiring
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.headers = {"content-length": str(len(payload))}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 8192):
        for start in range(0, len(self._payload), chunk_size):
            yield self._payload[start:start + chunk_size]


class _FakeClient:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def get_release(self, repo: str, version: str) -> dict:
        return {"id": 1, "tag_name": "v1"}

    def get_assets(self, repo: str, release_id: object) -> list[dict]:
        return [
            {
                "name": "asset.bin",
                "size": len(self._payload),
                "browser_download_url": "https://example.com/asset.bin",
            }
        ]

    @staticmethod
    def get_asset_download_url(asset: dict) -> str:
        return asset["browser_download_url"]

    def open_download(self, url: str, headers: dict[str, str] | None = None):
        return _FakeResponse(self._payload)


class _RecordingLimiter:
    bytes_per_second = 1

    def __init__(self) -> None:
        self.calls: list[int] = []

    def consume(self, nbytes: int, stop_event=None) -> None:
        self.calls.append(nbytes)


class TestDownloaderUsesLimiter:
    def test_limiter_consumes_whole_payload(self, tmp_path):
        payload = b"x" * 20_000
        limiter = _RecordingLimiter()
        manager = DownloadManager(_FakeClient(payload))
        progress: list[tuple[str, int, int, float]] = []

        result = manager.download_release(
            repo="owner/repo",
            pattern=["*.bin"],
            output_dir=str(tmp_path),
            sources=False,
            rate_limiter=limiter,
            progress_callback=lambda *args: progress.append(args),
        )

        assert result.downloaded == 1
        assert result.failed == 0
        assert sum(limiter.calls) == len(payload)
        assert progress
        assert progress[-1][1] == len(payload)
        assert os.path.getsize(tmp_path / "owner" / "repo" / "latest" / "asset.bin") == len(payload)