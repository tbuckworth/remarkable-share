// Drives the real popup.html + src/popup.js in jsdom against a fake chrome.* to
// check the Why box and mode choice reach the native host, and that the folder
// picker still restores lastFolder.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { JSDOM } from "jsdom";

const POPUP_HTML = readFileSync(new URL("../popup.html", import.meta.url), "utf8");
const OFFSET_ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/;

async function until(cond, what, ms = 2000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > ms) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 5));
  }
}

let loads = 0;

async function openPopup({ url, lastFolder, article, reply = { status: "ok", title: "Doc" } }) {
  const dom = new JSDOM(POPUP_HTML);
  if (dom.window.document.readyState !== "complete") {
    await new Promise((r) => dom.window.addEventListener("load", r));
  }
  const doc = dom.window.document;
  const sent = [];
  const stored = {};
  globalThis.document = doc;
  globalThis.chrome = {
    runtime: {
      sendNativeMessage(_host, msg, cb) {
        sent.push(msg);
        cb(msg.action === "folders" ? { folders: ["Articles", "Work"] } : reply);
      },
    },
    tabs: {
      query: async () => [{ id: 3, url }],
      sendMessage: async () => article,
    },
    scripting: { executeScript: async () => [] },
    storage: {
      local: {
        get: async () => ({ lastFolder }),
        set: async (o) => Object.assign(stored, o),
      },
    },
  };

  // A fresh module instance per popup, bound to this document.
  await import(`../src/popup.js?load=${++loads}`);
  doc.dispatchEvent(new dom.window.Event("DOMContentLoaded"));
  await until(() => doc.getElementById("folder").options.length > 1, "folders");

  const status = doc.getElementById("status");
  const done = () => until(() => /success|error/.test(status.className), "send to finish");
  return { dom, doc, sent, stored, status, done };
}

const ARTICLE = { title: "Post", content: "<p>x</p>", url: "https://example.com/post", domain: "example.com" };

test("note and mode from the popup reach the upload message; folder picker unchanged", async () => {
  const { doc, sent, stored, status, done } = await openPopup({
    url: ARTICLE.url,
    lastFolder: "Work",
    article: ARTICLE,
  });

  assert.equal(doc.getElementById("folder").value, "Work");
  assert.equal(doc.querySelector('input[name="mode"]:checked').value, "auto");
  assert.equal(doc.activeElement, doc.getElementById("note"));

  doc.getElementById("note").value = "Compare with Smith's take";
  doc.querySelector('input[name="mode"][value="personal"]').checked = true;
  doc.getElementById("send").click();
  await done();

  assert.equal(status.textContent, "Sent: Doc");
  assert.equal(stored.lastFolder, "Work");
  const uploads = sent.filter((m) => m.action === "upload");
  assert.equal(uploads.length, 1);
  const msg = uploads[0];
  assert.equal(msg.folder, "/Work");
  assert.equal(msg.note, "Compare with Smith's take");
  assert.equal(msg.mode, "personal");
  assert.equal(msg.source_url, ARTICLE.url);
  assert.match(msg.sent_at, OFFSET_ISO);
});

test("Enter in the Why box sends; empty note and Auto by default", async () => {
  const { dom, doc, sent, done } = await openPopup({ url: ARTICLE.url, article: ARTICLE });

  doc.getElementById("note").dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await done();

  const msg = sent.find((m) => m.action === "upload");
  assert.equal(msg.folder, "/");
  assert.equal(msg.note, "");
  assert.equal(msg.mode, "auto");
});

test("local PDF upload carries the fields; a host warning is shown", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ arrayBuffer: async () => new TextEncoder().encode("%PDF-1.4").buffer });
  try {
    const url = "file:///Users/titus/Downloads/some_paper.pdf";
    const { doc, sent, status, done } = await openPopup({
      url,
      reply: { status: "ok", title: "some paper", warning: "pypdf not importable by /usr/bin/python3" },
    });

    doc.getElementById("note").value = "for Tuesday";
    doc.querySelector('input[name="mode"][value="work"]').checked = true;
    doc.getElementById("send").click();
    await done();

    const msg = sent.find((m) => m.action === "upload_pdf");
    assert.equal(msg.data, Buffer.from("%PDF-1.4").toString("base64"));
    assert.equal(msg.note, "for Tuesday");
    assert.equal(msg.mode, "work");
    assert.equal(msg.source_url, url);
    assert.match(msg.sent_at, OFFSET_ISO);
    assert.equal(status.textContent, "Sent: some paper (pypdf not importable by /usr/bin/python3)");
  } finally {
    globalThis.fetch = realFetch;
  }
});
