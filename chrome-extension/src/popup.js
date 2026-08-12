import { isPdfUrl, nativeMessage, sendTab, titleFromPdfUrl, toFolderPath } from "./send.js";

const $ = (id) => document.getElementById(id);

async function readFileAsBase64(url) {
  const response = await fetch(url);
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 8192;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function showStatus(msg, type) {
  const el = $("status");
  el.textContent = msg;
  el.className = `status ${type}`;
}

document.addEventListener("DOMContentLoaded", async () => {
  // Get current tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $("url").textContent = tab?.url || "";

  // Load folders
  try {
    const { folders } = await nativeMessage({ action: "folders" });
    const sel = $("folder");
    sel.innerHTML = '<option value="/">/  (root)</option>';
    folders.forEach((f) => {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f;
      sel.appendChild(opt);
    });
    // Restore the last folder used, so the popup and context menu agree.
    const { lastFolder } = await chrome.storage.local.get("lastFolder");
    if (lastFolder && [...sel.options].some((o) => o.value === lastFolder)) {
      sel.value = lastFolder;
    }
  } catch (e) {
    console.error("Folder load error:", e);
    showStatus(`Cannot reach rmapi: ${e.message}`, "error");
  }

  $("send").addEventListener("click", async () => {
    $("send").disabled = true;
    showStatus("Processing...", "loading");

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const folder = $("folder").value;
      await chrome.storage.local.set({ lastFolder: folder });

      // Local PDFs must be read in the browser context to bypass macOS TCC
      // restrictions on the native host.
      if (isPdfUrl(tab.url) && tab.url.startsWith("file://")) {
        $("send").textContent = "Reading PDF...";
        showStatus("Reading local PDF...", "loading");
        let data;
        try {
          data = await readFileAsBase64(tab.url);
        } catch {
          showStatus(
            'Enable "Allow access to file URLs" in chrome://extensions for this extension',
            "error",
          );
          return;
        }
        $("send").textContent = "Uploading PDF...";
        const result = await nativeMessage({
          action: "upload_pdf",
          data,
          title: titleFromPdfUrl(tab.url),
          folder: toFolderPath(folder),
        });
        showStatus(`Sent: ${result.title}`, "success");
        return;
      }

      const result = await sendTab(tab, folder, {
        onStatus: (msg) => {
          $("send").textContent = msg.startsWith("Extracting") ? "Extracting..." : "Uploading...";
          showStatus(msg, "loading");
        },
      });
      showStatus(`Sent: ${result.title}`, "success");
    } catch (e) {
      showStatus(`Error: ${e.message}`, "error");
    } finally {
      $("send").disabled = false;
      $("send").textContent = "Send to reMarkable";
    }
  });
});
