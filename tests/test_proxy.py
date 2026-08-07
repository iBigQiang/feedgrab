"""Global proxy compatibility and propagation regressions."""

from feedgrab.fetchers import xhs_api
from feedgrab import cli
from feedgrab.service.models import DiagnosticResult
from feedgrab.service import proxy
from feedgrab.service.doctor import DoctorService


def _clear_proxy_env(monkeypatch):
    for key in (
        "FEEDGRAB_PROXY",
        "FEEDGRAB_PROXY_ENABLED",
        "FEEDGRAB_PROXY_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)


def test_single_feedgrab_proxy_variable_enables_proxy(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("FEEDGRAB_PROXY", "socks5://127.0.0.1:8567")

    assert proxy.is_proxy_enabled() is True
    assert proxy.get_proxy_url() == "socks5://127.0.0.1:8567"
    assert proxy.get_proxy_source() == "FEEDGRAB_PROXY"


def test_proxy_falls_back_to_https_then_http_environment(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://http-proxy:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy:8443")

    assert proxy.is_proxy_enabled() is True
    assert proxy.get_proxy_url() == "http://https-proxy:8443"
    assert proxy.get_proxy_source() == "HTTPS_PROXY"


def test_legacy_disabled_setting_overrides_system_proxy(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("FEEDGRAB_PROXY_ENABLED", "false")
    monkeypatch.setenv("FEEDGRAB_PROXY", "socks5://explicit-proxy:1080")
    monkeypatch.setenv("HTTPS_PROXY", "http://system-proxy:8443")

    assert proxy.is_proxy_enabled() is False
    assert proxy.get_proxy_url() == ""


def test_xhs_api_client_receives_active_proxy(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("FEEDGRAB_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setattr(xhs_api.httpx, "Client", FakeClient)
    monkeypatch.setattr(xhs_api, "_get_signing", lambda: None)

    client = xhs_api.XhsApiClient({})

    assert captured["proxy"] == "http://127.0.0.1:7890"
    client.close()


def test_ytdlp_proxy_args_use_active_proxy(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("FEEDGRAB_PROXY", "socks5://127.0.0.1:8567")

    assert proxy.get_ytdlp_proxy_args() == ["--proxy", "socks5://127.0.0.1:8567"]


def test_doctor_proxy_command_reports_source_and_connectivity(monkeypatch, capsys):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("FEEDGRAB_PROXY", "socks5://user:secret@127.0.0.1:8567")
    monkeypatch.setattr(
        DoctorService,
        "check_proxy_connectivity",
        lambda self: DiagnosticResult(
            name="proxy_connectivity",
            status="ok",
            message="代理连通",
            details={"target": "https://x.com", "status_code": 200},
        ),
    )

    cli.cmd_doctor("proxy")

    output = capsys.readouterr().out
    assert "FEEDGRAB_PROXY" in output
    assert "socks5://user:[redacted]@127.0.0.1:8567" in output
    assert "https://x.com" in output
    assert "代理连通" in output
