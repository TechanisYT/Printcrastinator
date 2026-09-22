import tomllib

from printcrastinator import secrets
from printcrastinator.config import Config, ExtraCalendar, load_config, save_config


def test_vault_roundtrip_and_no_plaintext_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRINTCRASTINATOR_SECRETS", "vault")
    cfg = Config()
    cfg.nextcloud.url, cfg.nextcloud.username = "https://x", "u"
    cfg.nextcloud.app_password = "hunter2"
    cfg.extra_calendars.append(ExtraCalendar("Uni", "caldav", "https://c", "me", "pw-uni"))
    p = save_config(cfg, tmp_path / "c.toml")
    raw = p.read_text()
    assert "hunter2" not in raw and "pw-uni" not in raw
    data = tomllib.loads(raw)
    assert data["nextcloud"]["app_password"].startswith("@vault:")
    assert cfg.nextcloud.app_password == "hunter2"  # live object untouched
    back = load_config(p)
    assert back.nextcloud.app_password == "hunter2"
    assert back.extra_calendars[0].password == "pw-uni"
    key = tmp_path / "printcrastinator" / "vault.key"
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert "hunter2" not in (tmp_path / "printcrastinator" / "vault.json").read_text()


def test_plaintext_config_is_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("PRINTCRASTINATOR_SECRETS", "vault")
    p = tmp_path / "c.toml"
    p.write_text('[nextcloud]\nurl = "https://x"\nusername = "u"\napp_password = "plain"\n')
    cfg = load_config(p)
    assert cfg.nextcloud.app_password == "plain"
    assert "plain" not in p.read_text()
    assert secrets.is_marker(tomllib.loads(p.read_text())["nextcloud"]["app_password"])
