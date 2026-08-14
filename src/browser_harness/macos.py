"""macOS-only helpers for local Chrome automation."""

from __future__ import annotations

import platform
import subprocess

from .daemon import profile_dirs, remote_debugging_toggle_profiles


_APPLESCRIPT = r'''using terms from application "System Events"
    on clickAllow(nodeRef)
        try
            if (role of nodeRef as text) is "AXButton" and ¬
                (description of nodeRef as text) is "Allow" then
                perform action "AXPress" of nodeRef
                return true
            end if
        end try
        try
            repeat with childRef in UI elements of nodeRef
                if my clickAllow(childRef) then return true
            end repeat
        end try
        return false
    end clickAllow
end using terms from

set resultText to "not-found"
tell application "System Events"
    if exists process "Google Chrome" then
        tell process "Google Chrome"
            repeat with w in windows
                try
                    repeat with s in sheets of w
                        if (name of s as text) is "Allow remote debugging?" then
                            if my clickAllow(s) then
                                set resultText to "clicked"
                                exit repeat
                            end if
                        end if
                    end repeat
                end try
                if resultText is "clicked" then exit repeat
            end repeat
        end tell
    end if
end tell
return resultText
'''


_ACCESSIBILITY_DETAIL = (
    "allow the app launching browser-harness (for example Terminal, iTerm, or Codex) "
    "in System Settings > Privacy & Security > Accessibility"
)


def _google_chrome_toggle_enabled() -> bool:
    """Only accept the toggle from the Google Chrome root used by the script."""
    chrome_root = profile_dirs("Darwin")[0]
    return chrome_root in remote_debugging_toggle_profiles()


def approve_remote_debugging() -> tuple[str, str | None]:
    """Click Chrome's exact per-connection Allow sheet without activating Chrome."""
    if platform.system() != "Darwin":
        return "unsupported", "mac-approve is only available on macOS"

    if not _google_chrome_toggle_enabled():
        return (
            "setup-required",
            'first enable "Allow remote debugging for this browser instance" at '
            "chrome://inspect/#remote-debugging, then run `browser-harness mac-approve` again",
        )

    try:
        completed = subprocess.run(
            ["osascript"],
            input=_APPLESCRIPT,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "accessibility-required", _ACCESSIBILITY_DETAIL
    except (OSError, subprocess.SubprocessError) as exc:
        return "error", str(exc)

    if completed.returncode != 0:
        detail = completed.stderr.strip() or "osascript failed"
        if "not authorized" in detail.lower() or "assistive" in detail.lower():
            return (
                "accessibility-required",
                _ACCESSIBILITY_DETAIL,
            )
        return "error", detail

    status = completed.stdout.strip()
    if status == "clicked":
        return "clicked", None
    if status == "not-found":
        return "not-found", None
    return "error", f"unexpected osascript result: {status or '<empty>'}"


def run_cli(args: list[str]) -> int:
    if args:
        print("usage: browser-harness mac-approve", flush=True)
        return 2

    status, detail = approve_remote_debugging()
    if detail:
        print(f"{status}: {detail}", flush=True)
    else:
        print(status, flush=True)
    return 0 if status == "clicked" else 1
