"""Validate deployment proxy configuration without exposing credentials."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from remote_labeling.backend.config import Settings


@pytest.mark.parametrize(
    "proxies",
    [
        None,
        {},
        {
            "http": "http://user:secret@proxy.example:35100",
            "https": "http://user:secret@proxy.example:35100",
        },
    ],
)
def test_load_download_proxies(tmp_path: Path, proxies: dict | None) -> None:
    """Read proxy settings from actual YAML alongside relative storage paths."""
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "state_dir": "state",
                "cache_dir": "cache",
                "export_dir": "exports",
                "download_proxies": proxies,
            }
        )
    )
    settings = Settings.load(path)
    assert settings.cache_dir == tmp_path / "cache"
    if proxies is None:
        assert settings.download_proxies is None
    else:
        assert {
            key: value.get_secret_value()
            for key, value in settings.download_proxies.items()
        } == proxies
    assert "user:secret" not in repr(settings)
    assert "user:secret" not in settings.model_dump_json()


@pytest.mark.parametrize(
    "proxies",
    [
        {"http": ""},
        {"http": "proxy.example:35100"},
        {"https": "socks5://user:secret@proxy.example:1080"},
        {"http": "http://user:secret@"},
        {"http": "http://user:secret@proxy.example:bad"},
        {"http": "http://user:secret@proxy.example:99999"},
        {"http": "http://user:secret@proxy.example/path"},
        {"http": "http://user:secret@proxy.example#fragment"},
        {"http": "http://user:secret@proxy.example\n"},
        {"ftp": "http://user:secret@proxy.example:35100"},
    ],
)
def test_reject_invalid_proxy_settings(tmp_path: Path, proxies: dict) -> None:
    """Reject unsupported URLs and keys before a download is attempted."""
    with pytest.raises(ValidationError) as error:
        Settings(
            state_dir=tmp_path,
            cache_dir=tmp_path,
            export_dir=tmp_path,
            download_proxies=proxies,
        )
    assert "download_proxies" in str(error.value)
    assert "user:secret" not in str(error.value)


def test_omitted_proxy_settings_inherit_environment(tmp_path: Path) -> None:
    """Existing configurations keep environment proxy discovery enabled."""
    settings = Settings(
        state_dir=tmp_path, cache_dir=tmp_path, export_dir=tmp_path
    )
    assert settings.download_proxies is None
