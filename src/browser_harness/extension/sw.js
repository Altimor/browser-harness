// Tab groups are tab-strip UI state, so CDP can't touch them -- chrome.tabs.group()
// is the only write API and it's extension-only. The harness attaches a CDP session
// to this worker and Runtime.evaluate's the functions below. Nothing runs on its own.

const MARKER = "\u{1F434}";  // horse emoji the harness puts on the tab it drives

// MV3 kills an idle worker after 30s, which would drop our only target. An alarm
// is the cheapest way to stay findable.
chrome.alarms.create("bh-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => {});

// A title carries no tab id, so scanning for the marker can't tell two marked tabs
// apart -- and grouping the wrong one is worse than not grouping. onUpdated does
// carry an id: the harness opens a claim, re-marks the tab it drives, and the
// event that lands names exactly that tab.
let claimedTabId = null;
let claiming = false;

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (claiming && typeof info.title === "string" && info.title.startsWith(MARKER)) {
    claimedTabId = tabId;
  }
});

self.bhSupported = () => !!(chrome.tabGroups && chrome.tabs.group);

self.bhBeginClaim = () => {
  claiming = true;
  claimedTabId = null;
  return true;
};

self.bhGroupClaimed = async (title, color) => {
  if (!self.bhSupported()) return { ok: false, reason: "unsupported" };

  let tabId = claimedTabId;
  if (tabId === null) {
    // Event hasn't landed. Fall back to the marker only when it's unambiguous.
    const marked = (await chrome.tabs.query({})).filter(
      (t) => (t.title || "").startsWith(MARKER)
    );
    if (marked.length === 1) tabId = marked[0].id;
    else return { ok: false, reason: marked.length ? "ambiguous" : "no-claim" };
  }

  let tab;
  try {
    tab = await chrome.tabs.get(tabId);
  } catch (e) {
    return { ok: false, reason: "stale-claim" };
  }

  try {
    // Reuse the task's existing group so repeated calls don't pile up groups.
    const [group] = await chrome.tabGroups.query({ title, windowId: tab.windowId });
    const groupId = await chrome.tabs.group(
      group
        ? { groupId: group.id, tabIds: [tabId] }
        : { tabIds: [tabId], createProperties: { windowId: tab.windowId } }
    );
    await chrome.tabGroups.update(groupId, { title, color });
    claiming = false;
    return { ok: true, groupId, tabId };
  } catch (e) {
    // Some Chromium forks (Dia) reject grouping outright -- caller degrades.
    return { ok: false, reason: String((e && e.message) || e) };
  }
};
