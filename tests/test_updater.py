import io
import json
import zipfile

import pytest

from app import updater


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def test_version_comparison_accepts_github_v_tags():
    assert updater.is_newer_version("v0.3.1", "0.3.0")
    assert updater.is_newer_version("0.4", "0.3.9")
    assert not updater.is_newer_version("v0.3.0", "0.3.0")
    assert not updater.is_newer_version("0.2.9", "0.3.0")
    with pytest.raises(updater.UpdateError):
        updater.is_newer_version("latest", "0.3.0")


def test_latest_release_selects_packaged_windows_zip(monkeypatch):
    document = {
        "tag_name": "v0.4.0",
        "name": "CoShell 0.4.0",
        "html_url": "https://github.com/dreamhartley/CoShell/releases/tag/v0.4.0",
        "published_at": "2026-07-24T12:00:00Z",
        "assets": [
            {
                "name": "checksums.txt",
                "browser_download_url": "https://github.com/dreamhartley/CoShell/releases/download/v0.4.0/checksums.txt",
                "size": 100,
            },
            {
                "name": "CoShell-v0.4.0-windows-x64-portable.zip",
                "browser_download_url": "https://github.com/dreamhartley/CoShell/releases/download/v0.4.0/CoShell.zip",
                "size": 1234,
                "digest": "sha256:abcd",
            },
        ],
    }
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(json.dumps(document).encode()),
    )

    release = updater.fetch_latest_release()

    assert release.version == "0.4.0"
    assert release.asset is not None
    assert release.asset.name == "CoShell-v0.4.0-windows-x64-portable.zip"
    assert updater.public_release_status(release)["update_available"] is True


def test_release_asset_selection_rejects_other_platforms():
    assets = [
        {
            "name": "CoShell-v0.4.0-Linux-x64.zip",
            "browser_download_url": "https://github.com/dreamhartley/CoShell/releases/download/v0.4.0/linux.zip",
            "size": 100,
        },
        {
            "name": "CoShell-v0.4.0-Windows-arm64.zip",
            "browser_download_url": "https://github.com/dreamhartley/CoShell/releases/download/v0.4.0/arm.zip",
            "size": 100,
        },
    ]

    assert updater._select_windows_asset(assets) is None


def test_untrusted_release_page_falls_back_to_repository_url():
    url = updater._trusted_release_url("https://example.test/download", "v0.4.0")

    assert url == "https://github.com/dreamhartley/CoShell/releases/tag/v0.4.0"


def test_release_archive_is_extracted_only_when_structure_is_complete(tmp_path):
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("CoShell/CoShell.exe", b"executable")
        package.writestr("CoShell/_internal/", b"")
        package.writestr("CoShell/_internal/app.pyz", b"content")

    payload = updater._safe_extract_release(archive, tmp_path / "output")

    assert payload.name == "CoShell"
    assert (payload / "CoShell.exe").read_bytes() == b"executable"
    assert (payload / "_internal" / "app.pyz").read_bytes() == b"content"


def test_release_archive_rejects_path_traversal(tmp_path):
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../CoShell.exe", b"not safe")

    with pytest.raises(updater.UpdateError, match="不安全"):
        updater._safe_extract_release(archive, tmp_path / "output")


def test_update_script_preserves_data_and_waits_for_parent():
    script = updater._UPDATER_SCRIPT
    assert 'Where-Object { $_.Name -ne "data" }' in script
    assert "Get-Process -Id $ParentProcessId" in script
    assert "Previous version restored and restarted" in script
