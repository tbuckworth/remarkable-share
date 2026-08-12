import { Readability } from "@mozilla/readability";
import { extractGmail, isGmail } from "./gmail.js";

function preserveMath(doc) {
  // CKEditor math elements (LessWrong): LaTeX in data-math-tex attribute
  doc.querySelectorAll(".ck-math-tex").forEach((el) => {
    const tex = el.getAttribute("data-math-tex");
    if (!tex) return;
    const display = el.classList.contains("ck-math-tex-display");
    const delim = display ? "$$" : "$";
    el.replaceWith(delim + tex + delim);
  });
  // KaTeX display math (katex-display wraps katex, so process first)
  doc.querySelectorAll(".katex-display").forEach((el) => {
    const ann = el.querySelector('annotation[encoding="application/x-tex"]');
    if (ann) el.replaceWith("\n$$" + ann.textContent + "$$\n");
  });
  // KaTeX inline math
  doc.querySelectorAll(".katex").forEach((el) => {
    const ann = el.querySelector('annotation[encoding="application/x-tex"]');
    if (ann) el.replaceWith("$" + ann.textContent + "$");
  });
  // MathJax v3 containers
  doc.querySelectorAll("mjx-container").forEach((el) => {
    const ann = el.querySelector('annotation[encoding="application/x-tex"]');
    if (ann) {
      const delim = el.hasAttribute("display") ? "$$" : "$";
      el.replaceWith(delim + ann.textContent + delim);
    }
  });
}

function install() {
  // The context menu tells us *that* a message was picked, not which one.
  // Remember what was under the cursor so extraction can target that message.
  let lastRightClicked = null;
  document.addEventListener(
    "contextmenu",
    (e) => {
      lastRightClicked = e.target;
    },
    true,
  );

  // Listen for extraction requests from the popup or the service worker
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type !== "extract") return;

    try {
      if (document.contentType === "application/pdf") {
        sendResponse({ pdf: true, url: location.href });
        return true;
      }

      if (isGmail()) {
        const email = extractGmail(document, msg.useContextTarget ? lastRightClicked : null);
        if (email) {
          sendResponse(email);
          return true;
        }
        // Fall through to Readability — a poor extraction beats none.
      }

      const clone = document.cloneNode(true);
      preserveMath(clone);
      const article = new Readability(clone).parse();

      if (!article) {
        sendResponse({ error: "Could not extract article content" });
        return;
      }

      // Get metadata from meta tags
      const getMeta = (name) => {
        const el =
          document.querySelector(`meta[property="${name}"]`) ||
          document.querySelector(`meta[name="${name}"]`);
        return el?.content || "";
      };

      sendResponse({
        title: article.title,
        content: article.content,
        byline: article.byline || getMeta("author") || getMeta("article:author"),
        siteName: article.siteName || "",
        publishedTime: getMeta("article:published_time") || getMeta("date"),
        url: location.href,
        domain: location.hostname.replace(/^www\./, ""),
      });
    } catch (e) {
      sendResponse({ error: e.message });
    }

    return true; // keep channel open for async response
  });
}

// This script is both declared for mail.google.com (so a listener is ready
// before the context-menu click) and injected on demand by the popup. Bail on
// the second load, or we'd register a duplicate listener and respond twice.
if (!window.__rmExtractorLoaded) {
  window.__rmExtractorLoaded = true;
  install();
}
