import pytest

from browser_harness import telemetry


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("wss://token@example.com/devtools/browser/123", "example.com"),
        ("http://127.0.0.1:9222", "127.0.0.1"),
        ("browser.example.com", "browser.example.com"),
        ("http://[", None),
    ],
)
def test_cdp_hostname(monkeypatch, value, expected):
    monkeypatch.delenv("BU_CDP_URL", raising=False)
    monkeypatch.setenv("BU_CDP_WS", value)
    assert telemetry._cdp_hostname() == expected
