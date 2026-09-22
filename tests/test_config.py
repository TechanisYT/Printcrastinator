from printcrastinator.config import Config, load_config, save_config


def test_roundtrip(tmp_path):
    cfg = Config()
    cfg.nextcloud.url = "https://cloud.example.org/"
    cfg.printer.feed_after_mm = 25
    cfg.daily.print_calendar_only_days = True
    p = save_config(cfg, tmp_path / "config.toml")
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    back = load_config(p)
    assert back.nextcloud.base_url == "https://cloud.example.org"
    assert back.printer.feed_after_mm == 25
    assert back.daily.print_calendar_only_days is True
    assert back.server.port == 8555


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml").printer.width_px == 384
