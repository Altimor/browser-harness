from pathlib import Path
from types import SimpleNamespace

from browser_harness import macos


def test_mac_approve_requires_the_persistent_chrome_checkbox(monkeypatch):
    monkeypatch.setattr(macos.platform, "system", lambda: "Darwin")
    chrome_root = Path("/tmp/Google Chrome")
    monkeypatch.setattr(macos, "profile_dirs", lambda system: [chrome_root, Path("/tmp/Edge")])
    monkeypatch.setattr(macos, "remote_debugging_toggle_profiles", lambda: [Path("/tmp/Edge")])

    status, detail = macos.approve_remote_debugging()

    assert status == "setup-required"
    assert "chrome://inspect/#remote-debugging" in detail


def test_mac_approve_runs_osascript_only_after_checkbox_is_enabled(monkeypatch):
    monkeypatch.setattr(macos.platform, "system", lambda: "Darwin")
    chrome_root = Path("/tmp/Google Chrome")
    monkeypatch.setattr(macos, "profile_dirs", lambda system: [chrome_root])
    monkeypatch.setattr(macos, "remote_debugging_toggle_profiles", lambda: [chrome_root])
    calls = []
    monkeypatch.setattr(
        macos.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or SimpleNamespace(returncode=0, stdout="clicked\n", stderr=""),
    )

    status, detail = macos.approve_remote_debugging()

    assert (status, detail) == ("clicked", None)
    assert calls[0][0] == (["osascript"],)
    assert "Allow remote debugging?" in calls[0][1]["input"]
    assert "AXPress" in calls[0][1]["input"]
    assert "activate" not in calls[0][1]["input"]


def test_mac_approve_returns_not_found_without_a_prompt(monkeypatch):
    monkeypatch.setattr(macos.platform, "system", lambda: "Darwin")
    chrome_root = Path("/tmp/Google Chrome")
    monkeypatch.setattr(macos, "profile_dirs", lambda system: [chrome_root])
    monkeypatch.setattr(macos, "remote_debugging_toggle_profiles", lambda: [chrome_root])
    monkeypatch.setattr(
        macos.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="not-found\n", stderr=""),
    )

    assert macos.approve_remote_debugging() == ("not-found", None)


def test_mac_approve_maps_pending_accessibility_consent_to_guidance(monkeypatch):
    monkeypatch.setattr(macos.platform, "system", lambda: "Darwin")
    chrome_root = Path("/tmp/Google Chrome")
    monkeypatch.setattr(macos, "profile_dirs", lambda system: [chrome_root])
    monkeypatch.setattr(macos, "remote_debugging_toggle_profiles", lambda: [chrome_root])
    monkeypatch.setattr(
        macos.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            macos.subprocess.TimeoutExpired(["osascript"], 5)
        ),
    )

    status, detail = macos.approve_remote_debugging()

    assert status == "accessibility-required"
    assert "Accessibility" in detail


def test_mac_approve_is_unavailable_off_macos(monkeypatch):
    monkeypatch.setattr(macos.platform, "system", lambda: "Linux")

    assert macos.approve_remote_debugging() == (
        "unsupported",
        "mac-approve is only available on macOS",
    )
