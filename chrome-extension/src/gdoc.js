// Google Docs extraction.
//
// The Docs editor paints its text onto a canvas, so the DOM Readability sees
// is chrome and accessibility scaffolding — the "article" it finds is junk.
// Instead we ask Docs for its HTML export, which the content script can fetch
// with the user's own cookies, and strip Google's styling back to structure.

const DOC_PATH_RE = /^\/document\/d\/([^/]+)/;

// Inline styling worth keeping: emphasis and super/subscript. Everything else
// (Arial 11pt, colours, 72pt page padding, fixed image sizes) fights CLEAN_CSS.
const KEEP_DECLARATIONS = new Set(["font-weight", "font-style", "text-decoration", "vertical-align"]);
const DEFAULT_VALUES = new Set(["400", "normal", "none", "baseline", "inherit"]);

export function isGoogleDoc(loc = location) {
  return loc.hostname === "docs.google.com" && DOC_PATH_RE.test(loc.pathname);
}

export function googleDocId(url) {
  try {
    const u = new URL(url);
    if (u.hostname !== "docs.google.com") return null;
    return DOC_PATH_RE.exec(u.pathname)?.[1] || null;
  } catch {
    return null;
  }
}

export function googleDocExportUrl(url) {
  const id = googleDocId(url);
  return id ? `https://docs.google.com/document/d/${id}/export?format=html` : null;
}

/** "My doc - Google Docs" → "My doc". */
export function googleDocTitle(tabTitle, fallback = "untitled") {
  const t = (tabTitle || "").replace(/\s*-\s*Google Docs\s*$/, "").trim();
  return t || fallback;
}

/** Google wraps outbound links in a redirect; unwrap so the PDF links go straight there. */
function unwrapRedirect(href) {
  try {
    const u = new URL(href);
    if (u.hostname === "www.google.com" && u.pathname === "/url") {
      return u.searchParams.get("q") || href;
    }
  } catch {
    /* relative or anchor link — leave alone */
  }
  return href;
}

function pruneStyle(el) {
  const style = el.getAttribute("style");
  if (style == null) return;
  const kept = [];
  for (const decl of style.split(";")) {
    const idx = decl.indexOf(":");
    if (idx < 0) continue;
    const prop = decl.slice(0, idx).trim().toLowerCase();
    const value = decl.slice(idx + 1).trim();
    if (KEEP_DECLARATIONS.has(prop) && !DEFAULT_VALUES.has(value.toLowerCase())) {
      kept.push(`${prop}:${value}`);
    }
  }
  if (kept.length) el.setAttribute("style", kept.join(";"));
  else el.removeAttribute("style");
}

/**
 * Turn a Docs HTML export into clean content for the renderer.
 *
 * @param {Document} doc parsed export (DOMParser in the browser, jsdom in tests)
 * @returns {string} inner HTML of the cleaned body
 */
export function cleanGoogleDocExport(doc) {
  const body = doc.body;

  // Docs' stylesheet is all page geometry and list counters; default list
  // rendering is what we want.
  body.querySelectorAll("style, script").forEach((el) => el.remove());

  // Nesting is expressed as sibling lists tagged with a level suffix
  // (lst-kix_abc-2), not nested <ul>s. Indent by level so structure survives.
  body.querySelectorAll("ul, ol").forEach((list) => {
    const level = /lst-kix_\S+?-(\d)\b/.exec(list.className)?.[1];
    list.removeAttribute("style");
    if (level && Number(level) > 0) {
      list.setAttribute("style", `margin-left:${Number(level) * 1.5}em`);
    }
  });

  body.querySelectorAll("*").forEach((el) => {
    if (!el.matches("ul, ol")) pruneStyle(el);
    el.removeAttribute("class");
    // Docs' per-heading ids are anchor targets for the table of contents.
    if (!/^h[1-6]$/i.test(el.tagName) && !el.id?.startsWith("ftnt")) el.removeAttribute("id");
    if (el.tagName === "IMG") {
      el.removeAttribute("width");
      el.removeAttribute("height");
    }
    if (el.tagName === "A" && el.hasAttribute("href")) {
      el.setAttribute("href", unwrapRedirect(el.getAttribute("href")));
    }
  });

  // Docs pads with empty paragraphs; CLEAN_CSS already spaces paragraphs.
  body.querySelectorAll("p").forEach((p) => {
    if (!p.textContent.trim() && !p.querySelector("img")) p.remove();
  });

  return body.innerHTML;
}

/**
 * Fetch and clean the current document.
 *
 * @param {object} opts
 * @param {string} opts.url tab URL
 * @param {string} opts.tabTitle document.title
 * @param {typeof fetch} [opts.fetchFn]
 * @param {(html: string) => Document} [opts.parse]
 */
export async function extractGoogleDoc({ url, tabTitle, fetchFn = fetch, parse } = {}) {
  const exportUrl = googleDocExportUrl(url);
  if (!exportUrl) return null;

  const res = await fetchFn(exportUrl, { credentials: "include" });
  if (!res.ok) {
    throw new Error(`Google Docs export failed (HTTP ${res.status}) — are you signed in with access to this doc?`);
  }
  const html = await res.text();
  const parser = parse || ((s) => new DOMParser().parseFromString(s, "text/html"));
  const doc = parser(html);

  const title = googleDocTitle(tabTitle, doc.title);
  return {
    title,
    content: cleanGoogleDocExport(doc),
    meta: "docs.google.com",
    url,
    domain: "docs.google.com",
  };
}
