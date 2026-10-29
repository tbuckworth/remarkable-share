// Service worker: the right-click path into the send pipeline.
//
// This exists because Chrome does not render the extension toolbar in popup
// windows — Gmail's "open in new window" pop-out has no extension icon. Context
// menus *do* appear there, so right-clicking an email is the only way to send
// one from a popped-out window.

import { sendTab } from "./send.js";

const MENU_ID = "send-email-to-remarkable";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: "Send this email to reMarkable",
    contexts: ["page", "selection", "link", "image"],
    documentUrlPatterns: ["https://mail.google.com/*"],
  });
});

async function badge(text, color) {
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setBadgeText({ text });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MENU_ID || !tab?.id) return;

  await badge("…", "#1e40af");
  try {
    const { lastFolder } = await chrome.storage.local.get("lastFolder");
    // No UI on this path, so no why-note: the PDF is stamped with mode "auto".
    const result = await sendTab(tab, lastFolder || "/", { useContextTarget: true, note: "", mode: "auto" });
    await badge("✓", "#166534");
    console.log(`Sent to reMarkable: ${result.title}`);
    if (result.warning) console.warn(`Send to reMarkable: ${result.warning}`);
  } catch (e) {
    await badge("✗", "#991b1b");
    console.error("Send to reMarkable failed:", e);
  }
  // The badge is the only feedback available without the notifications
  // permission, so leave it up long enough to notice.
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 5000);
});
