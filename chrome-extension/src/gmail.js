// Gmail-specific extraction.
//
// Readability can't handle Gmail: on the normal thread view it sees the whole
// SPA (nav, thread list, every message) and finds no single "article". So we
// pull the message out of the DOM directly.
//
// The same selectors cover the standalone views too — "View entire message"
// (?view=lg) and print (?view=pt) both render the body as div.a3s — so one
// code path serves every route into an email.
//
// These class names are obfuscated and Google can change them. Every lookup
// falls back, and extractGmail() returns null rather than throwing so the
// caller can drop back to Readability.

const BODY_SELECTOR = "div.a3s";

// Gmail's class names, such as they are:
//   a3s  message body        adn  expanded message container
//   hP   subject heading     gD   sender (name/email attrs)
//   g3   date (title attr)   ajR  "show trimmed content" toggle
const CONTAINER_SELECTORS = ["div.adn", "div.gs", "table.message", "div.ii"];

function isVisible(el) {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

export function isGmail(loc = location) {
  return loc.hostname === "mail.google.com";
}

/** Every message body on the page, visible ones preferred. */
function messageBodies(doc) {
  const all = [...doc.querySelectorAll(BODY_SELECTOR)];
  const visible = all.filter(isVisible);
  return visible.length ? visible : all;
}

function closestContainer(el) {
  for (const sel of CONTAINER_SELECTORS) {
    const found = el.closest(sel);
    if (found) return found;
  }
  return null;
}

/** The message body the user means: the one they right-clicked, else the last open one. */
function pickBody(doc, contextTarget) {
  if (contextTarget && doc.contains(contextTarget)) {
    const direct = contextTarget.closest(BODY_SELECTOR);
    if (direct) return direct;
    // Right-clicked the header rather than the body — take that message's body.
    const container = closestContainer(contextTarget);
    const inContainer = container?.querySelector(BODY_SELECTOR);
    if (inContainer) return inContainer;
  }
  const bodies = messageBodies(doc);
  // Gmail auto-expands the newest message, so last-open is what you're reading.
  return bodies[bodies.length - 1] || null;
}

/** "(3) Re: budget - me@gmail.com - Gmail" -> "Re: budget" */
function subjectFromTitle(title) {
  return (title || "")
    .replace(/\s*[-–]\s*Gmail\s*$/i, "")
    .replace(/\s*[-–]\s*\S+@\S+\s*$/, "")
    .replace(/^\(\d+\)\s*/, "")
    .trim();
}

function getSubject(doc) {
  const heading =
    doc.querySelector("h2.hP") ||
    doc.querySelector(".ha h2") ||
    doc.querySelector("h2[data-thread-perm-id]");
  const text = heading?.textContent?.trim();
  return text || subjectFromTitle(doc.title) || "Email";
}

function getSender(container, doc) {
  const el = container?.querySelector("span.gD") || doc.querySelector("span[email]");
  if (!el) return "";
  const name = el.getAttribute("name") || el.textContent?.trim() || "";
  const email = el.getAttribute("email") || "";
  if (name && email && name !== email) return `${name} <${email}>`;
  return name || email;
}

function getDate(container, doc) {
  const el = container?.querySelector("span.g3") || doc.querySelector("span.g3");
  if (!el) return "";
  // The title attr carries the full date; the text is often relative ("3 days ago").
  return (el.getAttribute("title") || el.textContent || "").trim();
}

/**
 * Strip the parts of a message body that hurt on e-ink.
 *
 * Quoted text is the previous message in the thread — redundant when you're
 * sending one email — but only drop it if real content remains, otherwise a
 * bare "> ..." reply would come through empty.
 */
function cleanBody(body) {
  const clone = body.cloneNode(true);

  clone.querySelectorAll("script, style, .ajR").forEach((el) => el.remove());

  // Inline attachments resolve to authenticated mail.google.com URLs that
  // headless Chrome can't fetch — they'd render as broken-image boxes.
  clone.querySelectorAll("img").forEach((img) => {
    const src = img.getAttribute("src") || "";
    if (src.startsWith("cid:") || /(^|\/\/)mail\.google\.com/.test(src)) img.remove();
  });

  const quotes = [...clone.querySelectorAll(".gmail_quote, blockquote.gmail_quote, .im")];
  if (quotes.length) {
    const quotedLength = quotes.reduce((len, q) => len + (q.textContent || "").length, 0);
    const remaining = (clone.textContent || "").length - quotedLength;
    if (remaining >= 200) quotes.forEach((q) => q.remove());
  }

  return clone.innerHTML;
}

/**
 * Extract the currently-open Gmail message.
 *
 * @param {Document} doc
 * @param {Element|null} contextTarget element the user right-clicked, if any
 * @returns {object|null} article shape matching the Readability path, or null
 */
export function extractGmail(doc, contextTarget = null) {
  const body = pickBody(doc, contextTarget);
  if (!body) return null;

  const content = cleanBody(body);
  if (!content.trim()) return null;

  const container = closestContainer(body);
  const sender = getSender(container, doc);
  const date = getDate(container, doc);

  return {
    title: getSubject(doc),
    content,
    byline: sender,
    siteName: "Gmail",
    // Gmail dates aren't ISO, so hand the caller a ready-made meta line rather
    // than letting it slice a date out of publishedTime.
    meta: [date, sender, "Gmail"].filter(Boolean).join(" · "),
    publishedTime: "",
    url: doc.location?.href || location.href,
    domain: "mail.google.com",
  };
}
