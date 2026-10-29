// The why-note fields (note, mode, source_url, sent_at) must ride along on every
// upload message the extension sends to the native host.

import assert from "node:assert/strict";
import test from "node:test";

import { isoWithOffset, provenance, sendTab } from "../src/send.js";

const OFFSET_ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/;

/** Minimal chrome.* for sendTab; returns the list of native messages sent. */
function fakeChrome({ article } = {}) {
  const sent = [];
  globalThis.chrome = {
    runtime: {
      sendNativeMessage(_host, msg, cb) {
        sent.push(msg);
        cb({ status: "ok", title: "Doc" });
      },
    },
    scripting: { executeScript: async () => [] },
    tabs: { sendMessage: async () => article },
  };
  return sent;
}

function withTZ(tz, fn) {
  const old = process.env.TZ;
  process.env.TZ = tz;
  try {
    return fn();
  } finally {
    if (old === undefined) delete process.env.TZ;
    else process.env.TZ = old;
  }
}

test("isoWithOffset gives local time with a numeric offset, never Z", () => {
  const d = new Date(Date.UTC(2026, 9, 29, 11, 9, 5));
  assert.equal(withTZ("UTC", () => isoWithOffset(d)), "2026-10-29T11:09:05+00:00");
  assert.equal(withTZ("Asia/Kolkata", () => isoWithOffset(d)), "2026-10-29T16:39:05+05:30");
  assert.equal(withTZ("America/New_York", () => isoWithOffset(d)), "2026-10-29T07:09:05-04:00");
  for (const tz of ["UTC", "Asia/Kolkata", "America/New_York", "Europe/London"]) {
    const s = withTZ(tz, () => isoWithOffset(d));
    assert.match(s, OFFSET_ISO);
    assert.equal(new Date(s).getTime(), d.getTime(), tz);
  }
});

test("provenance cleans the note and falls back to auto", () => {
  const p = provenance({ note: "  why\n\tthis  ", mode: "holiday", sourceUrl: "https://a.example/" });
  assert.equal(p.note, "why this");
  assert.equal(p.mode, "auto");
  assert.equal(p.source_url, "https://a.example/");
  assert.match(p.sent_at, OFFSET_ISO);
  assert.deepEqual(Object.keys(provenance()), ["note", "mode", "source_url", "sent_at"]);
  assert.equal(provenance({ note: null }).note, "");
});

// Readability articles, Gmail messages and Google Docs all reach the host as an
// "upload" message; only the article's shape differs.
const ARTICLES = {
  readability: {
    tab: "https://example.com/post",
    article: { title: "Post", content: "<p>x</p>", url: "https://example.com/post#s2", domain: "example.com" },
  },
  gmail: {
    tab: "https://mail.google.com/mail/u/0/#inbox/FMfcg",
    article: {
      title: "Re: budget",
      content: "<p>x</p>",
      meta: "1 Oct 2026 · Ann <ann@example.com> · Gmail",
      url: "https://mail.google.com/mail/u/0/#inbox/FMfcg",
      domain: "mail.google.com",
    },
  },
  gdoc: {
    tab: "https://docs.google.com/document/d/abc123/edit",
    article: {
      title: "Plan",
      content: "<p>x</p>",
      meta: "docs.google.com",
      url: "https://docs.google.com/document/d/abc123/edit",
      domain: "docs.google.com",
    },
  },
};

for (const [kind, { tab, article }] of Object.entries(ARTICLES)) {
  test(`${kind} upload carries note, mode, source URL and send time`, async () => {
    const sent = fakeChrome({ article });
    const result = await sendTab({ id: 7, url: tab }, "Articles", { note: "for the essay", mode: "work" });

    assert.deepEqual(result, { status: "ok", title: "Doc" });
    assert.equal(sent.length, 1);
    const msg = sent[0];
    assert.equal(msg.action, "upload");
    assert.equal(msg.title, article.title);
    assert.equal(msg.folder, "/Articles");
    assert.equal(msg.note, "for the essay");
    assert.equal(msg.mode, "work");
    assert.equal(msg.source_url, article.url);
    assert.match(msg.sent_at, OFFSET_ISO);
  });
}

test("direct PDF URL upload carries the fields, with the tab URL as source", async () => {
  const sent = fakeChrome();
  const url = "https://arxiv.org/pdf/2511.18397v2.pdf";
  await sendTab({ id: 7, url }, "/", { note: "cite in ch. 2", mode: "personal" });

  assert.equal(sent[0].action, "upload_pdf");
  assert.equal(sent[0].url, url);
  assert.equal(sent[0].note, "cite in ch. 2");
  assert.equal(sent[0].mode, "personal");
  assert.equal(sent[0].source_url, url);
  assert.match(sent[0].sent_at, OFFSET_ISO);
});

test("PDF found by the content script carries the fields too", async () => {
  const url = "https://example.com/download?id=3";
  const sent = fakeChrome({ article: { pdf: true, url } });
  await sendTab({ id: 7, url }, "/", { note: "n", mode: "work" });

  assert.equal(sent[0].action, "upload_pdf");
  assert.equal(sent[0].source_url, url);
  assert.equal(sent[0].note, "n");
  assert.equal(sent[0].mode, "work");
});

test("context-menu send (no options) sends an empty note and mode auto", async () => {
  const sent = fakeChrome({ article: ARTICLES.gmail.article });
  await sendTab({ id: 7, url: ARTICLES.gmail.tab }, "/", { useContextTarget: true });

  assert.equal(sent[0].note, "");
  assert.equal(sent[0].mode, "auto");
  assert.equal(sent[0].source_url, ARTICLES.gmail.article.url);
  assert.match(sent[0].sent_at, OFFSET_ISO);
});
