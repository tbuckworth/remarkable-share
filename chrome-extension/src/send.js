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
 * @param {(msg: string) => void} [opts.onStatus]
 * @returns {Promise<{title: string}>}
 */
export async function sendTab(tab, folder, opts = {}) {
  const { useContextTarget = false, onStatus = () => {} } = opts;
  const targetFolder = toFolderPath(folder);

  if (isPdfUrl(tab.url)) {
    onStatus("PDF detected — uploading directly...");
    return nativeMessage({
      action: "upload_pdf",
      url: tab.url,
      title: titleFromPdfUrl(tab.url),
      folder: targetFolder,
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
  });
}
