<img src="https://raw.githubusercontent.com/browser-use/media/main/browser-harness/banner-ink.svg" alt="Browser Harness" width="100%" />

# Browser Harness ♞

Connect an LLM directly to your real browser through one editable CDP websocket. The agent writes missing helpers as it works, so the harness improves with every task.

Try browser-harness in [Browser Use Cloud](https://cloud.browser-use.com/v4?utm_campaign=browser-harness-use-in-cloud&utm_source=github) or paste the setup prompt into your coding agent.

```
  ● agent: wants to upload a file
  │
  ● agent-workspace/agent_helpers.py → helper missing
  │
  ● agent writes it                         agent_helpers.py
  │                                                       + custom helper
  ✓ file uploaded
```

**You will never use the browser again.**

## Setup prompt

Paste into Claude Code or Codex:

```text
Install or upgrade browser-harness to the latest stable version with uv using Python 3.12, register the skill from `browser-harness skill`, and connect it to my browser. Ask whether I want local browser recordings enabled; default to no and preserve my existing preference on upgrades. Follow https://github.com/browser-use/browser-harness/blob/main/install.md if setup or connection fails.
```

The agent will open `chrome://inspect/#remote-debugging`. On first setup, tick
the checkbox so the agent can connect to your browser:

<img src="docs/setup-remote-debugging.png" alt="Remote debugging setup" width="520" style="border-radius: 12px;" />

## Example tasks

Set `BH_DOMAIN_SKILLS=1` to let the agent reuse site-specific playbooks:

- [Manage LinkedIn invitations](agent-workspace/domain-skills/linkedin/invitation-manager.md)
- [Search and extract Amazon products](agent-workspace/domain-skills/amazon/product-search.md)
- [Post to X](agent-workspace/domain-skills/x/posting.md)
- [Export a QuickBooks report](agent-workspace/domain-skills/qbo/report-export.md)
- [Upload a TikTok video](agent-workspace/domain-skills/tiktok/upload.md)

[Browse all domain skills →](agent-workspace/domain-skills/)

## Browser Use Cloud

Need stealth, parallel agents, or headless deployment? [Browser Use Cloud](https://cloud.browser-use.com/new-api-key) includes three concurrent browsers, proxies, and CAPTCHA solving on its free tier.

## How it works

- [`install.md`](install.md) handles first-time installation and browser setup.
- [`SKILL.md`](SKILL.md) teaches the agent how to use the harness.
- [`src/browser_harness/`](src/browser_harness/) is the protected core package.
- `${XDG_CONFIG_HOME:-~/.config}/browser-harness/agent-workspace/` holds helpers and domain skills the agent can edit.

Plain `browser-harness` helper calls attach to the running Chrome/Chromium CDP endpoint. For isolated automation, launch Chrome with `--remote-debugging-port` and pass `BU_CDP_URL`, or use a Browser Use cloud browser.

## Contributing

Bug fixes, documentation improvements, and agent-generated domain skills are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

[The Bitter Lesson of Agent Harnesses](https://browser-use.com/posts/bitter-lesson-agent-harnesses) · [Web Agents That Actually Learn](https://browser-use.com/posts/web-agents-that-actually-learn)
