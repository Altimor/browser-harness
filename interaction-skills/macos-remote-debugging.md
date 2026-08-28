# macOS Remote-Debugging Approval

Use this only when browser-harness reports that Chrome is waiting for its
"Allow remote debugging?" sheet. The sheet is native Chrome UI, outside CDP.

Leave the browser command running. In a second shell, use AppleScript UI
automation to press only the Allow button inside the exact sheet:

```bash
osascript <<'APPLESCRIPT'
using terms from application "System Events"
    on pressAllow(nodeRef)
        try
            if (role of nodeRef as text) is "AXButton" and ¬
                (description of nodeRef as text) is "Allow" then
                perform action "AXPress" of nodeRef
                return true
            end if
        end try
        try
            repeat with childRef in UI elements of nodeRef
                if my pressAllow(childRef) then return true
            end repeat
        end try
        return false
    end pressAllow
end using terms from

tell application "System Events"
    if exists process "Google Chrome" then
        tell process "Google Chrome"
            repeat with w in windows
                repeat with s in sheets of w
                    if (name of s as text) is "Allow remote debugging?" then
                        if my pressAllow(s) then return "ready"
                    end if
                end repeat
            end repeat
        end tell
    end if
end tell
return "not-found"
APPLESCRIPT
```

For Chromium, Edge, or Brave, substitute its macOS process name. Do not use a
generic coordinate click or activate Chrome unnecessarily. When the script
returns `ready`, the waiting browser command should continue; if it already
timed out, retry it once.

If macOS says the caller is not authorized to use assistive access, grant the
app launching the agent Accessibility permission in System Settings > Privacy
& Security > Accessibility. Do not loop: permission cannot be bypassed with a
different AppleScript wrapper.
