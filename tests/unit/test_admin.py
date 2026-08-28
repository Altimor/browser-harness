import json
import signal
from pathlib import Path

import pytest

from browser_harness import admin


class FakeSocket:
    def __init__(self, response=b'{"target_id":"target-1","session_id":"session-1","page":null}\n'):
        self.response = response
        self.closed = False
        self.sent = b""

    def sendall(self, data):
        self.sent += data

    def recv(self, _size):
        out, self.response = self.response, b""
        return out

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, pid=123, returncode=None):
        self.pid = pid
        self.returncode = returncode
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True


def test_cleanup_unattached_browser_launch_stops_posix_process_group(monkeypatch):
    process = FakeProcess()
    killed = []
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    monkeypatch.setattr("browser_harness.daemon._devtools_port_live", lambda _profile: False)
    monkeypatch.setattr(admin.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    admin._cleanup_unattached_browser_launch((process, Path("/profile")))

    assert killed == [(123, signal.SIGTERM)]


def test_cleanup_unattached_browser_launch_keeps_cdp_browser(monkeypatch):
    process = FakeProcess()
    monkeypatch.setattr("browser_harness.daemon._devtools_port_live", lambda _profile: True)
    monkeypatch.setattr(admin.os, "killpg", lambda _pid, _sig: pytest.fail("must keep the attached browser"))

    admin._cleanup_unattached_browser_launch((process, Path("/profile")))


def test_cleanup_unattached_browser_launch_ignores_unowned_launch(monkeypatch):
    monkeypatch.setattr(
        "browser_harness.daemon._devtools_port_live",
        lambda _profile: pytest.fail("must not probe an unowned launch"),
    )

    admin._cleanup_unattached_browser_launch((None, Path("/profile")))


@pytest.mark.parametrize("env_key", ["BH_CHROME_PATH", "CHROME_PATH"])
def test_explicit_chrome_path_retains_matching_profile_on_linux(monkeypatch, tmp_path, env_key):
    binary = tmp_path / "google-chrome-stable"
    binary.touch()
    profile = tmp_path / ".config" / "google-chrome"
    (profile / "Default").mkdir(parents=True)
    (profile / "Local State").write_text('{}')
    process = FakeProcess()

    other_key = "CHROME_PATH" if env_key == "BH_CHROME_PATH" else "BH_CHROME_PATH"
    monkeypatch.setenv(env_key, str(binary))
    monkeypatch.delenv(other_key, raising=False)
    monkeypatch.setattr("browser_harness.daemon.PROFILES", [profile])
    monkeypatch.setattr("browser_harness.daemon.remote_debugging_toggle_profiles", lambda: [profile])
    monkeypatch.setattr("browser_harness.daemon._devtools_port_live", lambda _profile: False)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("subprocess.Popen", lambda *_args, **_kwargs: process)
    killed = []
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    monkeypatch.setattr(admin.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    launch = admin._launch_browser()
    assert launch == (process, profile)

    admin._cleanup_unattached_browser_launch(launch)
    assert killed == [(process.pid, signal.SIGTERM)]


@pytest.mark.parametrize("system", ["Darwin", "Windows"])
def test_explicit_chrome_path_remains_unowned_without_platform_cleanup(monkeypatch, tmp_path, system):
    binary = tmp_path / ("chrome.exe" if system == "Windows" else "Google Chrome")
    binary.touch()
    profile = tmp_path / ".config" / "google-chrome"
    (profile / "Default").mkdir(parents=True)
    (profile / "Local State").write_text('{}')
    process = FakeProcess()

    monkeypatch.setenv("BH_CHROME_PATH", str(binary))
    monkeypatch.delenv("CHROME_PATH", raising=False)
    monkeypatch.setattr("browser_harness.daemon.PROFILES", [profile])
    monkeypatch.setattr("browser_harness.daemon.remote_debugging_toggle_profiles", lambda: [profile])
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("subprocess.Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(admin.os, "killpg", lambda *_args: pytest.fail("must not terminate an unowned browser"))

    launch = admin._launch_browser()
    assert launch == (process, None)

    admin._cleanup_unattached_browser_launch(launch)
    assert process.terminated is False


def test_explicit_unknown_browser_path_remains_unowned(monkeypatch, tmp_path):
    binary = tmp_path / "custom-browser"
    binary.touch()
    profile = tmp_path / ".config" / "google-chrome"
    profile.mkdir(parents=True)
    (profile / "Local State").write_text('{}')
    process = FakeProcess()

    monkeypatch.setenv("BH_CHROME_PATH", str(binary))
    monkeypatch.delenv("CHROME_PATH", raising=False)
    monkeypatch.setattr("browser_harness.daemon.PROFILES", [profile])
    monkeypatch.setattr("browser_harness.daemon.remote_debugging_toggle_profiles", lambda: [profile])
    monkeypatch.setattr("subprocess.Popen", lambda *_args, **_kwargs: process)

    assert admin._launch_browser() == (process, None)


@pytest.mark.parametrize("value", ["0", "false", "NO", " off "])
def test_update_banner_can_be_disabled_without_network_or_cache_access(monkeypatch, value):
    monkeypatch.setenv("BH_UPDATE_CHECK", value)
    monkeypatch.setattr(admin, "_cache_read", lambda: pytest.fail("cache should not be read"))
    monkeypatch.setattr(admin, "check_for_update", lambda: pytest.fail("network should not run"))

    admin.print_update_banner()


def test_update_banner_remains_enabled_by_default(monkeypatch):
    monkeypatch.delenv("BH_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(admin, "_cache_read", lambda: {"banner_shown_on": "1970-01-01"})
    called = []

    def fake_check_for_update():
        called.append(True)
        return "0.1.0", "0.1.0", False

    monkeypatch.setattr(admin, "check_for_update", fake_check_for_update)

    admin.print_update_banner()

    assert called == [True]


def test_local_chrome_mode_is_false_when_env_provides_remote_cdp():
    assert not admin._is_local_chrome_mode({"BU_CDP_WS": "ws://example.test/devtools/browser/1"})


def test_require_existing_daemon_fails_without_spawning(monkeypatch):
    monkeypatch.setattr(admin, "daemon_alive", lambda _name: False)

    with pytest.raises(RuntimeError, match="required daemon 'scoped' is not running"):
        admin.require_existing_daemon("scoped")


def test_require_existing_daemon_probes_cdp(monkeypatch):
    sock = FakeSocket(response=b'{"result":{"targetInfos":[]}}\n')
    monkeypatch.setattr(admin, "daemon_alive", lambda _name: True)
    monkeypatch.setattr(admin.ipc, "connect", lambda _name, timeout: (sock, None))

    admin.require_existing_daemon("scoped")

    assert b'"method": "Target.getTargets"' in sock.sent
    assert sock.closed is True


def test_strict_remote_stop_propagates_daemon_error(monkeypatch):
    sock = FakeSocket(response=b'{"error":"billing stop failed"}\n')
    monkeypatch.setattr(admin.ipc, "identify", lambda _name, timeout: 123)
    monkeypatch.setattr(admin, "_process_start_time", lambda _pid: 1)
    monkeypatch.setattr(admin.ipc, "connect", lambda _name, timeout: (sock, None))

    with pytest.raises(RuntimeError, match="billing stop failed"):
        admin.stop_remote_daemon("scoped")

    assert sock.closed is True


def test_remote_start_retries_cleanup_and_preserves_both_failures(monkeypatch):
    attempts = []
    monkeypatch.setattr(admin, "daemon_alive", lambda _name: False)
    monkeypatch.setattr(
        admin,
        "_browser_use",
        lambda path, method, body=None: (
            {"id": "browser-1", "cdpUrl": "https://cdp.example.test"}
            if method == "POST"
            else attempts.append((path, method, body))
            or (_ for _ in ()).throw(OSError("billing stop failed"))
        ),
    )
    monkeypatch.setattr(admin, "_cdp_ws_from_url", lambda _url: "wss://cdp.example.test/ws")
    monkeypatch.setattr(
        admin,
        "ensure_daemon",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("daemon start failed")),
    )
    monkeypatch.setattr(admin.time, "sleep", lambda _seconds: None)

    with pytest.raises(BaseExceptionGroup) as exc_info:
        admin.start_remote_daemon("scoped")

    assert [str(error) for error in exc_info.value.exceptions] == [
        "daemon start failed",
        "failed to stop remote browser browser-1: billing stop failed",
    ]
    assert len(attempts) == 3


def test_local_chrome_mode_is_false_when_process_env_provides_remote_cdp(monkeypatch):
    monkeypatch.setenv("BU_CDP_WS", "ws://example.test/devtools/browser/1")

    assert not admin._is_local_chrome_mode()


def test_handshake_timeout_needs_chrome_remote_debugging_prompt():
    msg = "CDP WS handshake failed: timed out during opening handshake"

    assert admin._needs_chrome_remote_debugging_prompt(msg)


def test_handshake_403_needs_chrome_remote_debugging_prompt():
    msg = "CDP WS handshake failed: server rejected WebSocket connection: HTTP 403"

    assert admin._needs_chrome_remote_debugging_prompt(msg)


def test_stale_websocket_does_not_open_chrome_inspect():
    msg = "no close frame received or sent"

    assert not admin._needs_chrome_remote_debugging_prompt(msg)


def test_ensure_daemon_automatically_approves_macos_popup(monkeypatch, tmp_path, capsys):
    alive_results = iter([False, False, True])
    approval_calls = []
    clock = iter([0.0, 3.0, 3.1, 3.2])

    monkeypatch.setattr(admin, "daemon_alive", lambda _name=None: next(alive_results))
    monkeypatch.setattr(admin, "_is_local_chrome_mode", lambda _env=None: True)
    monkeypatch.setattr(admin, "_log_tail", lambda _name=None: "handshake-wait: Allow remote debugging")
    monkeypatch.setattr(
        admin,
        "_try_macos_remote_debugging_approval",
        lambda name=None: approval_calls.append(name) or ("ready", None),
    )
    monkeypatch.setattr(admin.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(admin.ipc, "log_path", lambda _name: tmp_path / "daemon.log")
    monkeypatch.setattr(admin.ipc, "spawn_kwargs", lambda: {})
    monkeypatch.setattr(admin.time, "time", lambda: next(clock))
    monkeypatch.setattr(admin.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(admin, "_cleanup_unattached_browser_launch", lambda _launch: None)

    admin.ensure_daemon()

    assert approval_calls == [None]
    assert "approved Chrome's" in capsys.readouterr().err


def test_ensure_daemon_reports_macos_accessibility_recovery(monkeypatch, tmp_path):
    clock = iter([0.0, 3.0, 3.1, 61.0])
    # BH_ISOLATED_FALLBACK=0 keeps this on the manual-Allow path; the automatic
    # isolated-browser recovery is covered separately below.
    monkeypatch.setenv("BH_ISOLATED_FALLBACK", "0")

    monkeypatch.setattr(admin, "daemon_alive", lambda _name=None: False)
    monkeypatch.setattr(admin, "_is_local_chrome_mode", lambda _env=None: True)
    monkeypatch.setattr(admin, "_log_tail", lambda _name=None: "handshake-wait: Allow remote debugging")
    monkeypatch.setattr(
        admin,
        "_try_macos_remote_debugging_approval",
        lambda _name=None: ("accessibility-required", "grant Accessibility, then retry"),
    )
    monkeypatch.setattr(admin.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(admin.ipc, "log_path", lambda _name: tmp_path / "daemon.log")
    monkeypatch.setattr(admin.ipc, "spawn_kwargs", lambda: {})
    monkeypatch.setattr(admin.time, "time", lambda: next(clock))
    monkeypatch.setattr(admin.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(admin, "restart_daemon", lambda _name=None: None)

    with pytest.raises(RuntimeError, match="automatic macOS approval failed.*Accessibility.*retry"):
        admin.ensure_daemon()


def test_daemon_endpoint_names_discovers_valid_socket_names(tmp_path, monkeypatch):
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    monkeypatch.setattr(admin.ipc, "BH_RUNTIME_DIR", None)  # shared-tmpdir mode
    monkeypatch.setattr(admin.ipc, "_RUNTIME", tmp_path)
    (tmp_path / "bu-default.sock").touch()
    (tmp_path / "bu-remote_1.sock").touch()
    (tmp_path / "bu-invalid.name.sock").touch()
    (tmp_path / "not-bu-default.sock").touch()

    assert admin._daemon_endpoint_names() == ["default", "remote_1"]


def test_daemon_endpoint_names_with_bh_runtime_dir_returns_local_name_when_sock_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    monkeypatch.setattr(admin.ipc, "BH_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(admin.ipc, "BH_RUNTIME_DIR_SHARED", False)
    monkeypatch.setattr(admin.ipc, "_RUNTIME", tmp_path)
    monkeypatch.setattr(admin, "NAME", "session-xyz")
    (tmp_path / "bu.sock").touch()

    assert admin._daemon_endpoint_names() == ["session-xyz"]


def test_daemon_endpoint_names_with_bh_runtime_dir_returns_empty_when_sock_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    monkeypatch.setattr(admin.ipc, "BH_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(admin.ipc, "BH_RUNTIME_DIR_SHARED", False)
    monkeypatch.setattr(admin.ipc, "_RUNTIME", tmp_path)
    monkeypatch.setattr(admin, "NAME", "session-xyz")

    assert admin._daemon_endpoint_names() == []


def test_daemon_endpoint_names_with_shared_bh_runtime_dir_discovers_named_sockets(tmp_path, monkeypatch):
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    monkeypatch.setattr(admin.ipc, "BH_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(admin.ipc, "BH_RUNTIME_DIR_SHARED", True)
    monkeypatch.setattr(admin.ipc, "_RUNTIME", tmp_path)
    (tmp_path / "bu-default.sock").touch()
    (tmp_path / "bu-work.sock").touch()
    (tmp_path / "bu-invalid.name.sock").touch()
    (tmp_path / "bu.sock").touch()  # stale isolated-runtime endpoint

    assert admin._daemon_endpoint_names() == ["default", "work"]


def test_active_browser_connections_counts_only_healthy_daemons(monkeypatch):
    monkeypatch.setattr(admin, "_daemon_endpoint_names", lambda: ["default", "stale", "remote"])

    def fake_connect(name, timeout=1.0):
        if name == "stale":
            raise ConnectionRefusedError()
        if name == "remote":
            return FakeSocket(b'{"error":"no close frame received or sent"}\n'), None
        return FakeSocket(), None

    monkeypatch.setattr(admin.ipc, "connect", fake_connect)

    assert admin.active_browser_connections() == 1


def test_daemon_browser_ready_checks_the_selected_daemon(monkeypatch):
    calls = []
    monkeypatch.setattr(
        admin,
        "_daemon_browser_connection",
        lambda name: calls.append(name) or {"name": name, "page": None},
    )

    assert admin.daemon_browser_ready("work")
    assert calls == ["work"]


def test_active_browser_connections_skips_daemons_reporting_cdp_disconnected(monkeypatch):
    monkeypatch.setattr(admin, "_daemon_endpoint_names", lambda: ["default", "stale"])

    def fake_connect(name, timeout=1.0):
        if name == "stale":
            return FakeSocket(b'{"error":"cdp_disconnected"}\n'), None
        return FakeSocket(), None

    monkeypatch.setattr(admin.ipc, "connect", fake_connect)

    assert admin.active_browser_connections() == 1


def test_browser_connections_returns_attached_page(monkeypatch):
    monkeypatch.setattr(admin, "_daemon_endpoint_names", lambda: ["default"])
    response = (
        b'{"target_id":"target-1","session_id":"session-1",'
        b'"page":{"targetId":"target-1","title":"Cat - Wikipedia","url":"https://en.wikipedia.org/wiki/Cat"}}\n'
    )
    monkeypatch.setattr(admin.ipc, "connect", lambda name, timeout=1.0: (FakeSocket(response), None))

    assert admin.browser_connections() == [
        {
            "name": "default",
            "page": {"title": "Cat - Wikipedia", "url": "https://en.wikipedia.org/wiki/Cat"},
        }
    ]


def test_chrome_running_detects_helium_on_linux(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "subprocess.check_output",
        lambda *args, **kwargs: "systemd\nhelium\nxdg-desktop-portal\n",
    )

    assert admin._chrome_running()


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/snap/chromium/1234/usr/lib/chromium-browser/chromium-browser", True),
        ("/SNAP/foo", True),
        ("/usr/bin/google-chrome-stable", False),
        ("", False),
    ],
)
def test_is_snap_browser(path, expected):
    assert admin._is_snap_browser(path) == expected


def test_doctor_probe_preserves_snap_bin_env_symlink(monkeypatch, tmp_path):
    target = tmp_path / "usr" / "bin" / "snap"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n")
    snap_bin = tmp_path / "snap" / "bin"
    snap_bin.mkdir(parents=True)
    chromium = snap_bin / "chromium"
    chromium.symlink_to(target)

    monkeypatch.setenv("BH_CHROME_PATH", str(chromium))
    monkeypatch.delenv("CHROME_PATH", raising=False)

    name, path = admin._doctor_probe_chrome_binary_for_snap()

    assert name == "chromium"
    assert path == str(chromium)
    assert admin._is_snap_browser(path)


def test_doctor_probe_preserves_snap_bin_path_symlink(monkeypatch, tmp_path):
    target = tmp_path / "usr" / "bin" / "snap"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n")
    snap_bin = tmp_path / "snap" / "bin"
    snap_bin.mkdir(parents=True)
    chromium = snap_bin / "chromium"
    chromium.symlink_to(target)

    monkeypatch.delenv("BH_CHROME_PATH", raising=False)
    monkeypatch.delenv("CHROME_PATH", raising=False)

    def fake_which(cmd):
        return str(chromium) if cmd == "chromium" else None

    monkeypatch.setattr("shutil.which", fake_which)

    name, path = admin._doctor_probe_chrome_binary_for_snap()

    assert name == "chromium"
    assert path == str(chromium)
    assert admin._is_snap_browser(path)


def test_run_doctor_prints_snap_detect_on_linux_when_probe_is_snap(monkeypatch, capsys):
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: False)
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "browser_connections", lambda: [])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_doctor_probe_chrome_binary_for_snap", lambda: ("chromium", "/snap/chromium/1/usr/bin/chromium"))
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)

    assert admin.run_doctor() == 1

    out = capsys.readouterr().out
    assert "[snap-detect]" in out
    assert "Browser: chromium (snap)" in out
    assert "Snap confinement prevents CDP binding" in out
    assert "docs/snap-linux-headless.md" in out


def test_run_doctor_skips_snap_detect_on_non_linux(monkeypatch, capsys):
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(admin, "browser_connections", lambda: [])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_doctor_probe_chrome_binary_for_snap", lambda: ("chromium", "/snap/chromium/1/usr/bin/chromium"))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)

    assert admin.run_doctor() == 0

    out = capsys.readouterr().out
    assert "[snap-detect]" not in out


def test_run_doctor_reports_bad_stored_cloud_auth_without_crashing(monkeypatch, capsys):
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(admin, "browser_connections", lambda: [])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_doctor_probe_chrome_binary_for_snap", lambda: (None, None))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(admin.auth, "auth_status", lambda: (_ for _ in ()).throw(admin.auth.AuthError("auth file is not valid JSON")))

    assert admin.run_doctor() == 0

    out = capsys.readouterr().out
    assert "Browser Use cloud auth" in out
    assert "auth file is not valid JSON" in out


def test_run_doctor_fix_snap_prints_steps(capsys):
    assert admin.run_doctor_fix_snap() == 0
    out = capsys.readouterr().out
    assert "browser-harness doctor --fix-snap" in out
    assert "BH_CHROME_PATH" in out
    assert "google-chrome-stable_current_amd64.deb" in out
    assert "browser-harness --doctor" in out


def test_run_doctor_prints_active_browser_connections_and_active_pages(monkeypatch, capsys):
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(admin, "browser_connections", lambda: [
        {
            "name": "default",
            "page": {"title": "Example", "url": "https://example.test"},
        },
        {
            "name": "cats",
            "page": {"title": "Cat - Wikipedia", "url": "https://en.wikipedia.org/wiki/Cat"},
        },
    ])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)

    assert admin.run_doctor() == 0

    out = capsys.readouterr().out
    assert "[ok  ] active browser connections — 2" in out
    assert "        default — active page: Example — https://example.test" in out
    assert "        cats — active page: Cat - Wikipedia — https://en.wikipedia.org/wiki/Cat" in out


def test_doctor_page_output_truncates_long_text(monkeypatch, capsys):
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(admin, "DOCTOR_TEXT_LIMIT", 20)
    monkeypatch.setattr(admin, "browser_connections", lambda: [
        {
            "name": "default",
            "page": {"title": "A very long page title", "url": "https://example.test/very/long/path"},
        }
    ])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)

    assert admin.run_doctor() == 0

    out = capsys.readouterr().out
    assert "A very long page ..." in out
    assert "https://example.t..." in out


def test_start_remote_daemon_stops_created_browser_when_daemon_start_fails(monkeypatch):
    calls = []
    browser = {"id": "browser-123", "cdpUrl": "http://127.0.0.1:9333", "liveUrl": "https://live.example"}

    def fake_browser_use(path, method, body=None):
        calls.append((path, method, body))
        if (path, method) == ("/browsers", "POST"):
            return browser
        if (path, method) == ("/browsers/browser-123", "PATCH"):
            return {}
        raise AssertionError((path, method, body))

    monkeypatch.setattr(admin, "daemon_alive", lambda name: False)
    monkeypatch.setattr(admin, "_browser_use", fake_browser_use)
    monkeypatch.setattr(admin, "_cdp_ws_from_url", lambda url: "ws://example.test/devtools/browser/1")
    monkeypatch.setattr(admin, "ensure_daemon", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        admin.start_remote_daemon()

    assert calls == [
        ("/browsers", "POST", {}),
        ("/browsers/browser-123", "PATCH", {"action": "stop"}),
    ]


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_start_remote_daemon_stops_created_browser_when_daemon_start_is_interrupted(monkeypatch, exc_type):
    calls = []
    browser = {"id": "browser-123", "cdpUrl": "http://127.0.0.1:9333", "liveUrl": "https://live.example"}

    def fake_browser_use(path, method, body=None):
        calls.append((path, method, body))
        if (path, method) == ("/browsers", "POST"):
            return browser
        if (path, method) == ("/browsers/browser-123", "PATCH"):
            return {}
        raise AssertionError((path, method, body))

    monkeypatch.setattr(admin, "daemon_alive", lambda name: False)
    monkeypatch.setattr(admin, "_browser_use", fake_browser_use)
    monkeypatch.setattr(admin, "_cdp_ws_from_url", lambda url: "ws://example.test/devtools/browser/1")
    monkeypatch.setattr(admin, "ensure_daemon", lambda **kwargs: (_ for _ in ()).throw(exc_type()))

    with pytest.raises(exc_type):
        admin.start_remote_daemon()

    assert calls == [
        ("/browsers", "POST", {}),
        ("/browsers/browser-123", "PATCH", {"action": "stop"}),
    ]


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_stop_cloud_browser_swallows_baseexception_from_stop_request(monkeypatch, exc_type):
    monkeypatch.setattr(admin, "_browser_use", lambda *args, **kwargs: (_ for _ in ()).throw(exc_type()))

    admin._stop_cloud_browser("browser-123")

def test_start_remote_daemon_does_not_stop_created_browser_on_success(monkeypatch):
    calls = []
    browser = {"id": "browser-123", "cdpUrl": "http://127.0.0.1:9333", "liveUrl": "https://live.example"}

    def fake_browser_use(path, method, body=None):
        calls.append((path, method, body))
        if (path, method) == ("/browsers", "POST"):
            return browser
        raise AssertionError((path, method, body))

    monkeypatch.setattr(admin, "daemon_alive", lambda name: False)
    monkeypatch.setattr(admin, "_browser_use", fake_browser_use)
    monkeypatch.setattr(admin, "_cdp_ws_from_url", lambda url: "ws://example.test/devtools/browser/1")
    monkeypatch.setattr(admin, "ensure_daemon", lambda **kwargs: None)
    monkeypatch.setattr(admin, "_show_live_url", lambda url: None)

    assert admin.start_remote_daemon() == browser
    assert calls == [
        ("/browsers", "POST", {}),
    ]


# --- restart_daemon: PID-reuse safety ---

def test_restart_daemon_does_not_signal_when_daemon_unreachable(monkeypatch, tmp_path):
    """If ipc.identify() returns None (daemon gone), restart_daemon must NOT
    fall back to reading the pid file and SIGTERMing whatever owns that PID —
    that's the PID-reuse hazard. It should only clean up files."""
    pid_path = tmp_path / "default.pid"
    # A pid file with a PID that, if signaled, would hit an unrelated process.
    # The whole point is that we don't read or trust this number.
    pid_path.write_text("99999")

    kill_calls = []
    monkeypatch.setattr(admin.os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))
    monkeypatch.setattr(admin.ipc, "identify", lambda name, timeout=5.0: None)
    monkeypatch.setattr(admin.ipc, "ping", lambda name, timeout=1.0: False)
    monkeypatch.setattr(admin.ipc, "pid_path", lambda name: pid_path)
    monkeypatch.setattr(admin.ipc, "cleanup_endpoint", lambda name: None)

    # Should not raise, should not signal, should still clean up the pid file.
    admin.restart_daemon("default")

    assert kill_calls == [], (
        f"restart_daemon SIGTERM'd a PID despite identify() returning None — "
        f"this is the PID-reuse hazard the function is meant to avoid. Calls: {kill_calls}"
    )
    assert not pid_path.exists(), "stale pid file should be cleaned up"


def test_restart_daemon_signals_pid_returned_by_identify_not_pid_file(monkeypatch, tmp_path):
    """The PID we signal must come from the live daemon's self-report, never
    from the pid file. If a stale pid file disagrees, the live daemon's PID wins."""
    import signal

    pid_path = tmp_path / "default.pid"
    pid_path.write_text("99999")  # bogus stale value — must be ignored

    live_pid = 4242

    kill_calls = []
    def fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        # First os.kill(pid, 0) probe: report process is gone so we exit the loop
        # without escalating. We just want to see WHICH pid was probed.
        if sig == 0:
            raise ProcessLookupError

    class FakeIPC:
        def __init__(self):
            self.shutdown_sent = False
        def identify(self, name, timeout=5.0):
            return live_pid
        def connect(self, name, timeout):
            return ("conn", "tok")
        def request(self, conn, tok, msg):
            if msg.get("meta") == "shutdown":
                self.shutdown_sent = True
            return {"ok": True}
        def pid_path(self, name):
            return pid_path
        def cleanup_endpoint(self, name):
            pass

    fake = FakeIPC()
    monkeypatch.setattr(admin.os, "kill", fake_kill)
    monkeypatch.setattr(admin.ipc, "identify", fake.identify)
    monkeypatch.setattr(admin.ipc, "ping", lambda name, timeout=1.0: True)
    monkeypatch.setattr(admin.ipc, "connect", fake.connect)
    monkeypatch.setattr(admin.ipc, "request", fake.request)
    monkeypatch.setattr(admin.ipc, "pid_path", fake.pid_path)
    monkeypatch.setattr(admin.ipc, "cleanup_endpoint", fake.cleanup_endpoint)

    admin.restart_daemon("default")

    assert fake.shutdown_sent, "expected shutdown IPC to be sent"
    assert kill_calls, "expected at least one os.kill probe"
    pids_signaled = {pid for pid, _ in kill_calls}
    assert pids_signaled == {live_pid}, (
        f"restart_daemon must only signal the PID returned by identify(); "
        f"signaled pids: {pids_signaled}, expected {{{live_pid}}} (and NOT 99999)"
    )
    assert not pid_path.exists()


def test_restart_daemon_sends_shutdown_to_pre_upgrade_daemon_without_pid_in_ping(monkeypatch, tmp_path):
    """Backward compat: a pre-upgrade daemon's ping reply has {pong:True} but
    no `pid` field, so identify() returns None. The shutdown IPC must STILL be
    sent (so the daemon exits cleanly), but no os.kill happens (we have no
    verified PID to safely signal)."""
    pid_path = tmp_path / "default.pid"
    pid_path.write_text("99999")  # bogus stale value

    kill_calls = []
    shutdown_calls = []

    def fake_request(conn, tok, msg):
        if msg.get("meta") == "shutdown":
            shutdown_calls.append(msg)
        return {"ok": True}

    monkeypatch.setattr(admin.os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))
    monkeypatch.setattr(admin.ipc, "identify", lambda name, timeout=5.0: None)
    monkeypatch.setattr(admin.ipc, "ping", lambda name, timeout=1.0: True)  # old daemon: alive but no pid
    monkeypatch.setattr(admin.ipc, "connect", lambda name, timeout: ("conn", "tok"))
    monkeypatch.setattr(admin.ipc, "request", fake_request)
    monkeypatch.setattr(admin.ipc, "pid_path", lambda name: pid_path)
    monkeypatch.setattr(admin.ipc, "cleanup_endpoint", lambda name: None)

    admin.restart_daemon("default")

    assert shutdown_calls, (
        "restart_daemon must send shutdown IPC to a pre-upgrade daemon even "
        "when identify() can't return a PID — otherwise upgrades orphan the "
        "old daemon while deleting its socket and pid file."
    )
    assert kill_calls == [], (
        f"no os.kill should fire when we don't have a verified PID, "
        f"but got: {kill_calls}"
    )
    assert not pid_path.exists()


def test_restart_daemon_skips_sigterm_if_pid_was_reused_during_wait(monkeypatch, tmp_path):
    """A second identify() runs immediately before the SIGTERM. If the daemon
    exited and the PID was reused mid-wait, identify() will return None (or a
    different PID) and we must NOT signal — that's the PID-reuse race during
    the 15s wait window."""
    import signal

    pid_path = tmp_path / "default.pid"
    pid_path.write_text("99999")
    live_pid = 4242

    kill_calls = []

    def fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        # All os.kill(pid, 0) probes succeed → loop exhausts → reaches the
        # SIGTERM branch. (We're simulating a "wedged" daemon that the wait
        # loop can't tell apart from a daemon whose PID got reused.)

    # First identify() call (top of restart_daemon) returns the live PID.
    # Second identify() call (right before SIGTERM) returns None — simulating
    # the daemon having exited and its PID having been reused by an unrelated
    # process. The function must NOT escalate to SIGTERM in that state.
    identify_responses = iter([live_pid, None])
    monkeypatch.setattr(admin.os, "kill", fake_kill)
    monkeypatch.setattr(admin.ipc, "identify", lambda name, timeout=5.0: next(identify_responses))
    monkeypatch.setattr(admin.ipc, "ping", lambda name, timeout=1.0: True)
    monkeypatch.setattr(admin.ipc, "connect", lambda name, timeout: ("conn", "tok"))
    monkeypatch.setattr(admin.ipc, "request", lambda conn, tok, msg: {"ok": True})
    monkeypatch.setattr(admin.ipc, "pid_path", lambda name: pid_path)
    monkeypatch.setattr(admin.ipc, "cleanup_endpoint", lambda name: None)
    # Speed up the wait loop so the test finishes quickly. The loop polls 75
    # times at 0.2s = 15s; with sleep neutralized it runs in microseconds.
    monkeypatch.setattr(admin.time, "sleep", lambda _s: None)

    admin.restart_daemon("default")

    sigterms = [(pid, sig) for pid, sig in kill_calls if sig == signal.SIGTERM]
    assert sigterms == [], (
        f"restart_daemon issued SIGTERM despite the re-verify identify() "
        f"returning None (PID was reused during the 15s wait). Calls: {kill_calls}"
    )
    assert not pid_path.exists()


def test_restart_daemon_sigterms_via_start_time_fingerprint_when_socket_gone(monkeypatch, tmp_path):
    """Slow-shutdown recovery: the daemon's serve() tears down the IPC socket
    BEFORE the process exits (the daemon then runs slow cleanup like remote
    `stop` PATCH calls that can hang). In that window, identify() returns None
    even though the process is still our daemon. SIGTERM must still fire when
    the PID's start-time fingerprint hasn't changed since we first identified
    it — that's strong evidence of "same process, just slow to exit."
    """
    import signal

    pid_path = tmp_path / "default.pid"
    pid_path.write_text("99999")
    live_pid = 4242

    kill_calls = []

    def fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        # All os.kill(pid, 0) probes succeed; loop exhausts → SIGTERM gate runs.

    # First identify() returns live_pid. Second identify() returns None — the
    # daemon has torn down its IPC during shutdown but the process is still
    # finishing up cleanup work, so the start-time fingerprint is unchanged.
    identify_responses = iter([live_pid, None])
    # Both _process_start_time() calls return the same fingerprint, signaling
    # "still the same process." This is the legitimate-slow-shutdown case.
    monkeypatch.setattr(admin, "_process_start_time", lambda pid: "STARTED_AT_X")
    monkeypatch.setattr(admin.os, "kill", fake_kill)
    monkeypatch.setattr(admin.ipc, "identify", lambda name, timeout=5.0: next(identify_responses))
    monkeypatch.setattr(admin.ipc, "ping", lambda name, timeout=1.0: True)
    monkeypatch.setattr(admin.ipc, "connect", lambda name, timeout: ("conn", "tok"))
    monkeypatch.setattr(admin.ipc, "request", lambda conn, tok, msg: {"ok": True})
    monkeypatch.setattr(admin.ipc, "pid_path", lambda name: pid_path)
    monkeypatch.setattr(admin.ipc, "cleanup_endpoint", lambda name: None)
    monkeypatch.setattr(admin.time, "sleep", lambda _s: None)

    admin.restart_daemon("default")

    sigterms = [(pid, sig) for pid, sig in kill_calls if sig == signal.SIGTERM]
    assert sigterms == [(live_pid, signal.SIGTERM)], (
        f"slow-shutdown daemon (identify=None but unchanged start-time) must "
        f"still receive SIGTERM. signal calls: {kill_calls}"
    )


def test_restart_daemon_skips_sigterm_when_start_time_changed_during_wait(monkeypatch, tmp_path):
    """If the start-time fingerprint of the original PID has CHANGED, the PID
    was reused by another process. Even though identify() also returns None,
    we must skip SIGTERM — start-time mismatch is the signal that protects
    against killing an unrelated reused-PID process."""
    import signal

    pid_path = tmp_path / "default.pid"
    pid_path.write_text("99999")
    live_pid = 4242

    kill_calls = []
    monkeypatch.setattr(admin.os, "kill", lambda pid, sig: kill_calls.append((pid, sig)))

    identify_responses = iter([live_pid, None])
    # First start-time read at top of restart_daemon: "ORIGINAL".
    # Second start-time read in the safety gate: "DIFFERENT" — proof of reuse.
    start_time_responses = iter(["ORIGINAL", "DIFFERENT"])
    monkeypatch.setattr(admin, "_process_start_time", lambda pid: next(start_time_responses))
    monkeypatch.setattr(admin.ipc, "identify", lambda name, timeout=5.0: next(identify_responses))
    monkeypatch.setattr(admin.ipc, "ping", lambda name, timeout=1.0: True)
    monkeypatch.setattr(admin.ipc, "connect", lambda name, timeout: ("conn", "tok"))
    monkeypatch.setattr(admin.ipc, "request", lambda conn, tok, msg: {"ok": True})
    monkeypatch.setattr(admin.ipc, "pid_path", lambda name: pid_path)
    monkeypatch.setattr(admin.ipc, "cleanup_endpoint", lambda name: None)
    monkeypatch.setattr(admin.time, "sleep", lambda _s: None)

    admin.restart_daemon("default")

    sigterms = [(pid, sig) for pid, sig in kill_calls if sig == signal.SIGTERM]
    assert sigterms == [], (
        f"start-time mismatch indicates PID reuse — restart_daemon must NOT "
        f"SIGTERM. signal calls: {kill_calls}"
    )


# --- _process_start_time helper ---

def test_process_start_time_returns_stable_fingerprint_for_self():
    """The start-time of the current process should be readable on Linux,
    macOS, and Windows, and stable across two reads."""
    import os as _os, sys
    if sys.platform.startswith("linux") or sys.platform == "darwin" or sys.platform == "win32":
        pid = _os.getpid()
        first = admin._process_start_time(pid)
        second = admin._process_start_time(pid)
        assert first is not None, "expected a fingerprint for the current PID"
        assert first == second, (
            f"two reads of the same PID should return the same fingerprint; "
            f"got {first!r} vs {second!r}"
        )


def test_process_start_time_returns_none_for_invalid_pid():
    """Bad inputs (None, 0, negatives, non-int) and PIDs with no live process
    must return None rather than raising."""
    for bad in (None, 0, -1, -42, "not-an-int", 1.5, True, False):
        assert admin._process_start_time(bad) is None, (
            f"expected None for invalid pid {bad!r}"
        )
    # 2**31 - 1 is the largest pid_t; in practice no live process at that PID.
    assert admin._process_start_time((1 << 31) - 1) is None


# --- isolated automation browser fallback (issue #661) -----------------------


class FakeBrowserProcess(FakeProcess):
    """Popen stand-in that boots like Chrome: writes DevToolsActivePort."""

    def __init__(self, argv, port=51234):
        super().__init__(pid=4242)
        self.argv = argv
        user_data_dir = next(
            a.split("=", 1)[1] for a in argv if a.startswith("--user-data-dir=")
        )
        (Path(user_data_dir) / "DevToolsActivePort").write_text(
            f"{port}\n/devtools/browser/abc\n", encoding="utf-8"
        )


def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("BH_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BH_CONFIG_DIR", raising=False)
    monkeypatch.delenv("BH_ISOLATED_FALLBACK", raising=False)
    # Every recorded browser carries a start-time fingerprint; fake PIDs have none.
    monkeypatch.setattr(admin, "_process_start_time", lambda pid: f"boot-{pid}")


def _fake_browser_binary(monkeypatch, tmp_path, name="Google Chrome"):
    binary = tmp_path / name
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(admin, "_isolated_browser_binaries", lambda: [binary])
    return binary


@pytest.mark.parametrize(
    "platform_name,env,expected",
    [
        ("darwin", None, True),
        ("darwin", "0", False),
        ("darwin", " OFF ", False),
        ("linux", None, False),
        ("linux", "1", True),
    ],
)
def test_isolated_fallback_is_macos_default_and_env_overridable(monkeypatch, platform_name, env, expected):
    monkeypatch.setattr(admin.sys, "platform", platform_name)
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    if env is None:
        monkeypatch.delenv("BH_ISOLATED_FALLBACK", raising=False)
    else:
        monkeypatch.setenv("BH_ISOLATED_FALLBACK", env)

    assert admin._isolated_fallback_enabled() is expected


def test_start_isolated_browser_uses_its_own_profile_and_free_port(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    binary = _fake_browser_binary(monkeypatch, tmp_path)
    launched = []
    monkeypatch.setattr(
        admin.subprocess, "Popen",
        lambda argv, **_kwargs: launched.append(argv) or FakeBrowserProcess(argv),
    )

    record = admin._start_isolated_browser("smoke")

    argv = launched[0]
    user_data_dir = admin.paths.isolated_browser_dir("smoke")
    assert argv[0] == str(binary)
    assert f"--user-data-dir={user_data_dir}" in argv
    assert "--remote-debugging-port=0" in argv  # browser picks the port: no race
    assert "--remote-debugging-address=127.0.0.1" in argv  # never off-loopback
    assert not any(a.startswith("--profile-directory") for a in argv)
    assert user_data_dir.is_relative_to(tmp_path / "home")
    assert record["cdp_url"] == "http://127.0.0.1:51234"
    assert json.loads(admin.paths.isolated_browser_record("smoke").read_text()) == {
        "pid": 4242, "port": 51234, "user_data_dir": str(user_data_dir),
        "start_time": "boot-4242",
    }


def test_start_isolated_browser_replaces_the_one_it_already_owns(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    _fake_browser_binary(monkeypatch, tmp_path)
    admin._write_isolated_record(
        "smoke", {"pid": 111, "port": 9, "user_data_dir": "/old", "start_time": "boot-111"}
    )
    killed = []
    # The predecessor stays "owned" until it is actually signalled, so the stop
    # really has to observe it exit before the replacement launches.
    monkeypatch.setattr(admin, "_isolated_browser_owned", lambda record: record["pid"] not in dict(killed))
    monkeypatch.setattr(admin.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(admin.subprocess, "Popen", lambda argv, **_kwargs: FakeBrowserProcess(argv))

    record = admin._start_isolated_browser("smoke")

    assert killed == [(111, signal.SIGTERM)]
    assert record["pid"] == 4242


def test_start_isolated_browser_refuses_while_its_predecessor_is_still_running(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    _fake_browser_binary(monkeypatch, tmp_path)
    admin._write_isolated_record(
        "smoke", {"pid": 111, "port": 9, "user_data_dir": "/old", "start_time": "boot-111"}
    )
    monkeypatch.setattr(admin, "_isolated_browser_owned", lambda _record: True)  # never exits
    monkeypatch.setattr(admin, "ISOLATED_BROWSER_STOP_TIMEOUT", 0)
    monkeypatch.setattr(admin.os, "killpg", lambda *_a: None)
    monkeypatch.setattr(admin.os, "kill", lambda *_a: pytest.fail("must not escalate to SIGKILL"))
    monkeypatch.setattr(
        admin.subprocess, "Popen",
        lambda *_a, **_k: pytest.fail("must not launch a second browser on a locked profile"),
    )

    assert admin._start_isolated_browser("smoke") is None
    # The record survives so a later stop can still identify that exact process.
    assert admin.paths.isolated_browser_record("smoke").exists()


def test_start_isolated_browser_returns_none_and_leaves_nothing_running(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    _fake_browser_binary(monkeypatch, tmp_path)
    process = FakeProcess(pid=777, returncode=1)  # browser died before writing its port
    monkeypatch.setattr(admin.subprocess, "Popen", lambda _argv, **_kwargs: process)
    killed = []
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    monkeypatch.setattr(admin.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    assert admin._start_isolated_browser("smoke") is None
    assert killed == [(777, signal.SIGTERM)]
    assert not admin.paths.isolated_browser_record("smoke").exists()


def test_isolated_browser_owned_requires_the_pid_to_hold_our_profile_dir(monkeypatch):
    import os as _os

    # Real process, real `ps`: this test's own PID never holds our user-data-dir.
    own = {"pid": _os.getpid(), "user_data_dir": "/tmp/bh-none",
           "start_time": str(admin._process_start_time(_os.getpid()))}
    assert admin._isolated_browser_owned(own) is False
    monkeypatch.setattr(admin, "_process_start_time", lambda pid: "boot-1")
    monkeypatch.setattr(
        admin.subprocess, "check_output",
        lambda *_a, **_k: "/Applications/Google Chrome --user-data-dir=/tmp/bh-x --remote-debugging-port=0\n",
    )
    live = {"pid": 1, "start_time": "boot-1"}
    assert admin._isolated_browser_owned({**live, "user_data_dir": "/tmp/bh-x"}) is True
    assert admin._isolated_browser_owned({**live, "user_data_dir": "/tmp/bh-other"}) is False
    # A profile path that merely prefixes the running one is not a match.
    assert admin._isolated_browser_owned({**live, "user_data_dir": "/tmp/bh"}) is False


def test_isolated_browser_owned_rejects_a_reused_pid(monkeypatch):
    monkeypatch.setattr(admin, "_process_start_time", lambda pid: "boot-NEW")
    monkeypatch.setattr(
        admin.subprocess, "check_output",
        lambda *_a, **_k: pytest.fail("a stale fingerprint must be rejected before `ps`"),
    )

    assert admin._isolated_browser_owned(
        {"pid": 1, "user_data_dir": "/tmp/bh-x", "start_time": "boot-OLD"}
    ) is False


def test_stop_isolated_browser_never_signals_a_browser_it_does_not_own(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    admin._write_isolated_record(
        "smoke", {"pid": 111, "port": 9, "user_data_dir": "/old", "start_time": "boot-111"}
    )
    monkeypatch.setattr(admin, "_isolated_browser_owned", lambda _record: False)
    monkeypatch.setattr(admin.os, "killpg", lambda *_a: pytest.fail("must not signal an unowned browser"))
    monkeypatch.setattr(admin.os, "kill", lambda *_a: pytest.fail("must not signal an unowned browser"))

    assert admin._stop_isolated_browser("smoke") is True
    assert not admin.paths.isolated_browser_record("smoke").exists()


@pytest.mark.parametrize(
    "stale",
    [
        {"pid": 111, "port": 9, "user_data_dir": "/old"},  # pre-fingerprint record
        {"pid": 111, "port": 9, "user_data_dir": "/old", "start_time": ""},
        {"pid": 111, "port": 9, "user_data_dir": "/old", "start_time": 17},
        {"pid": True, "port": 9, "user_data_dir": "/old", "start_time": "boot-1"},
        {"port": 9, "user_data_dir": "/old", "start_time": "boot-1"},
    ],
)
def test_incomplete_record_is_dropped_and_never_signalled(monkeypatch, tmp_path, stale):
    _isolated_home(monkeypatch, tmp_path)
    admin.paths.isolated_browser_record("smoke").write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(admin.os, "killpg", lambda *_a: pytest.fail("must not signal on a stale record"))
    monkeypatch.setattr(admin.os, "kill", lambda *_a: pytest.fail("must not signal on a stale record"))

    assert admin._read_isolated_record("smoke") is None
    assert admin._stop_isolated_browser("smoke") is True
    assert not admin.paths.isolated_browser_record("smoke").exists()


def test_stop_isolated_browser_waits_for_the_recorded_process_to_exit(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    admin._write_isolated_record(
        "smoke", {"pid": 111, "port": 9, "user_data_dir": "/old", "start_time": "boot-111"}
    )
    alive = iter([True, True, False])
    monkeypatch.setattr(admin, "_isolated_browser_owned", lambda _record: next(alive))
    monkeypatch.setattr(admin.os, "killpg", lambda *_a: None)
    monkeypatch.setattr(admin.time, "sleep", lambda _seconds: None)

    assert admin._stop_isolated_browser("smoke") is True
    assert not admin.paths.isolated_browser_record("smoke").exists()


def test_stop_isolated_browser_reports_failure_without_escalating(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    admin._write_isolated_record(
        "smoke", {"pid": 111, "port": 9, "user_data_dir": "/old", "start_time": "boot-111"}
    )
    monkeypatch.setattr(admin, "_isolated_browser_owned", lambda _record: True)
    monkeypatch.setattr(admin, "ISOLATED_BROWSER_STOP_TIMEOUT", 0)
    signals = []
    monkeypatch.setattr(admin.os, "killpg", lambda pid, sig: signals.append(sig))

    assert admin._stop_isolated_browser("smoke") is False
    assert signals == [signal.SIGTERM]  # never SIGKILL
    assert admin.paths.isolated_browser_record("smoke").exists()


def test_isolated_browser_dir_refuses_a_symlinked_profile(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    users_chrome = tmp_path / "Users-Chrome"
    users_chrome.mkdir()
    (admin.paths.isolated_browsers_dir() / "smoke").symlink_to(users_chrome)

    with pytest.raises(RuntimeError, match="symlink"):
        admin.paths.isolated_browser_dir("smoke")


def test_isolated_browser_dir_refuses_a_symlinked_root(monkeypatch, tmp_path):
    _isolated_home(monkeypatch, tmp_path)
    root = admin.paths.isolated_browsers_dir()
    users_chrome = tmp_path / "Users-Chrome"
    users_chrome.mkdir()
    root.rmdir()
    root.symlink_to(users_chrome)

    with pytest.raises(RuntimeError, match="symlink"):
        admin.paths.isolated_browser_dir("smoke")


@pytest.mark.parametrize("name", ["..", "../../Chrome", "a/b", ""])
def test_isolated_browser_dir_refuses_escaping_names(monkeypatch, tmp_path, name):
    _isolated_home(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="unsafe isolated browser name"):
        admin.paths.isolated_browser_dir(name)


def test_start_isolated_browser_gives_up_when_the_profile_path_is_unsafe(monkeypatch, tmp_path, capsys):
    _isolated_home(monkeypatch, tmp_path)
    _fake_browser_binary(monkeypatch, tmp_path)
    users_chrome = tmp_path / "Users-Chrome"
    users_chrome.mkdir()
    (admin.paths.isolated_browsers_dir() / "smoke").symlink_to(users_chrome)
    monkeypatch.setattr(
        admin.subprocess, "Popen",
        lambda *_a, **_k: pytest.fail("must not launch Chrome on a redirected profile path"),
    )

    assert admin._start_isolated_browser("smoke") is None
    assert "profile unusable" in capsys.readouterr().err


def test_restart_daemon_stops_the_isolated_browser_it_owns(monkeypatch, tmp_path):
    monkeypatch.setattr(admin.ipc, "identify", lambda *_a, **_k: None)
    monkeypatch.setattr(admin.ipc, "ping", lambda *_a, **_k: False)
    monkeypatch.setattr(admin.ipc, "cleanup_endpoint", lambda _name: None)
    monkeypatch.setattr(admin.ipc, "pid_path", lambda _name: tmp_path / "bu.pid")
    stopped = []
    monkeypatch.setattr(admin, "_stop_isolated_browser", lambda name: stopped.append(name))

    admin.restart_daemon("smoke")

    assert stopped == ["smoke"]


def _permission_blocked_ensure_daemon(monkeypatch, tmp_path, approval=("accessibility-required", "grant Accessibility")):
    """Drive ensure_daemon into the exact macOS permission-blocked failure of #661."""
    _isolated_home(monkeypatch, tmp_path)
    clock = iter([0.0, 3.0, 3.1, 61.0])
    monkeypatch.setattr(admin, "daemon_alive", lambda _name=None: False)
    monkeypatch.setattr(admin, "_is_local_chrome_mode", lambda _env=None: True)
    monkeypatch.setattr(admin, "_log_tail", lambda _name=None: "handshake-wait: Allow remote debugging")
    monkeypatch.setattr(admin, "_try_macos_remote_debugging_approval", lambda _name=None: approval)
    monkeypatch.setattr(admin.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(admin.ipc, "log_path", lambda _name: tmp_path / "daemon.log")
    monkeypatch.setattr(admin.ipc, "spawn_kwargs", lambda: {})
    monkeypatch.setattr(admin.time, "time", lambda: next(clock))
    monkeypatch.setattr(admin.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(admin, "restart_daemon", lambda _name=None: None)
    monkeypatch.setattr(
        admin, "_browser_use",
        lambda *_a, **_k: pytest.fail("local recovery must never call the cloud API"),
    )


def test_ensure_daemon_recovers_unattended_with_an_isolated_browser(monkeypatch, tmp_path, capsys):
    _permission_blocked_ensure_daemon(monkeypatch, tmp_path)
    monkeypatch.setattr(admin, "_isolated_fallback_enabled", lambda: True)
    monkeypatch.setattr(
        admin, "_start_isolated_browser",
        lambda name: {"pid": 4242, "port": 51234, "user_data_dir": f"/iso/{name}",
                      "cdp_url": "http://127.0.0.1:51234"},
    )
    real_ensure_daemon = admin.ensure_daemon
    retries = []
    monkeypatch.setattr(admin, "ensure_daemon", lambda **kwargs: retries.append(kwargs))

    real_ensure_daemon(name="smoke")

    assert retries == [{"wait": 60.0, "name": "smoke", "env": {"BU_CDP_URL": "http://127.0.0.1:51234"}}]
    err = capsys.readouterr().err
    assert "isolated profile (/iso/smoke)" in err
    # Precise claim: separate profile, not a permanently signed-out browser.
    assert "does NOT inherit the user's Chrome logins, cookies, or tabs" in err
    assert "signed out" not in err


def test_ensure_daemon_stops_the_isolated_browser_when_it_cannot_be_attached(monkeypatch, tmp_path):
    _permission_blocked_ensure_daemon(monkeypatch, tmp_path)
    monkeypatch.setattr(admin, "_isolated_fallback_enabled", lambda: True)
    monkeypatch.setattr(
        admin, "_start_isolated_browser",
        lambda name: {"pid": 4242, "port": 51234, "user_data_dir": f"/iso/{name}",
                      "cdp_url": "http://127.0.0.1:51234"},
    )
    stopped = []
    monkeypatch.setattr(admin, "_stop_isolated_browser", lambda name: stopped.append(name))
    real_ensure_daemon = admin.ensure_daemon
    monkeypatch.setattr(
        admin, "ensure_daemon",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("isolated browser unreachable")),
    )

    with pytest.raises(RuntimeError, match="isolated browser unreachable"):
        real_ensure_daemon(name="smoke")

    assert stopped == ["smoke"]
