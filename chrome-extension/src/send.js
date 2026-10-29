// Shared send pipeline, used by both the popup and the context menu.

const NATIVE_HOST = "com.titus.web2pdf";

export function nativeMessage(msg) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(NATIVE_HOST, msg, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else if (response?.error) {
        reject(new Error(response.error));
      } else {
        resolve(response);
      }
    });
  });
}

export function isPdfUrl(url) {
  try {
    return new URL(url).pathname.toLowerCase().endsWith(".pdf");
  } catch {
    return false;
  }
}

export function titleFromPdfUrl(url) {
  try {
    const path = new URL(url).pathname;
    const filename = decodeURIComponent(path.split("/").pop());
    return filename.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").trim() || "untitled";
  } catch {
    return "untitled";
  }
}

/** Folder names come back bare from rmapi; the host wants a rooted path. */
export function toFolderPath(folder) {
  if (!folder || folder === "/") return "/";
  return folder.startsWith("/") ? folder : `/${folder}`;
}

/** Reading modes the popup offers; the host falls back to "auto" for anything else. */
export const MODES = ["auto", "work", "personal"];

/**
 * Local time with a numeric UTC offset, e.g. 2026-10-29T11:09:05+00:00.
 * toISOString() would give a trailing Z, which Python < 3.11 can't parse.
 */
export function isoWithOffset(date = new Date()) {
  const pad = (n) => String(Math.trunc(Math.abs(n))).padStart(2, "0");
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
    `${sign}${pad(offset / 60)}:${pad(offset % 60)}`
  );
}

/**
 * The why-note fields every upload message carries. The host writes them into the
 * PDF's metadata (/Web2pdfNote, /Web2pdfMode, /Web2pdfUrl, /Web2pdfSentAt).
 */
export function provenance({ note = "", mode = "auto", sourceUrl = "", now = new Date() } = {}) {
  return {
    note: String(note ?? "").replace(/\s+/g, " ").trim(),
    mode: MODES.includes(mode) ? mode : "auto",
    source_url: sourceUrl || "",
    sent_at: isoWithOffset(now),
  };
}

/** The Gmail extractor supplies its own meta line; articles get one built here. */
function metaLine(article) {
  if (article.meta) return article.meta;
  const parts = [];
  if (article.publishedTime) parts.push(article.publishedTime.slice(0, 10));
  if (article.byline) parts.push(article.byline);
  parts.push(article.domain);
  return parts.join(" · ");
}

/**
 * Extract the given tab and upload it.
 *
 * @param {chrome.tabs.Tab} tab
 * @param {string} folder rmapi folder path
 * @param {object} [opts]
 * @param {boolean} [opts.useContextTarget] target the right-clicked message
 * @param {string} [opts.note] "Why am I saving this?" — empty when there's no UI
 * @param {string} [opts.mode] one of MODES
 * @param {(msg: string) => void} [opts.onStatus]
 * @returns {Promise<{title: string, warning?: string}>}
 */
export async function sendTab(tab, folder, opts = {}) {
  const { useContextTarget = false, note = "", mode = "auto", onStatus = () => {} } = opts;
  const targetFolder = toFolderPath(folder);
  // Stamp the click, not whenever extraction finishes.
  const now = new Date();
  const stamp = (sourceUrl) => provenance({ note, mode, sourceUrl, now });

  if (isPdfUrl(tab.url)) {
    onStatus("PDF detected — uploading directly...");
    return nativeMessage({
      action: "upload_pdf",
      url: tab.url,
      title: titleFromPdfUrl(tab.url),
      folder: targetFolder,
      ...stamp(tab.url),
    });
  }

  onStatus("Extracting...");
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["dist/content.js"],
  });

  const article = await chrome.tabs.sendMessage(tab.id, { type: "extract", useContextTarget });

  // Content script found a PDF whose URL didn't end in .pdf
  if (article?.pdf) {
    onStatus("PDF detected — uploading directly...");
    return nativeMessage({
      action: "upload_pdf",
      url: article.url,
      title: titleFromPdfUrl(article.url),
      folder: targetFolder,
      ...stamp(article.url),
    });
  }

  if (article?.error) throw new Error(`Extraction failed: ${article.error}`);
  if (!article) throw new Error("No response from page");

  onStatus("Rendering PDF and uploading...");
  return nativeMessage({
    action: "upload",
    title: article.title,
    content: article.content,
    meta: metaLine(article),
    folder: targetFolder,
    ...stamp(article.url || tab.url),
  });
}
