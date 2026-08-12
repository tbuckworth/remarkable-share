// Fixtures reproduce Gmail's real DOM shape (obfuscated class names and all).
// jsdom reports every element as zero-sized, so these exercise the "no element
// measures visible — fall back to all of them" path as well.

import assert from "node:assert/strict";
import test from "node:test";
import { JSDOM } from "jsdom";

import { extractGmail, isGmail } from "../src/gmail.js";

const HREF = "https://mail.google.com/mail/u/0/#inbox/FMfcgz";

function docFrom(body, { title = "Quarterly numbers - me@gmail.com - Gmail" } = {}) {
  const dom = new JSDOM(`<!DOCTYPE html><html><head><title>${title}</title></head><body>${body}</body></html>`, {
    url: HREF,
  });
  return dom.window.document;
}

function message({ subject = "Quarterly numbers", sender = "Alice Smith", email = "alice@example.com", date = "Thu, Jul 30, 2026, 9:14 AM", body = "<div>Hello there.</div>" } = {}) {
  return `
    <div class="ha"><h2 class="hP">${subject}</h2></div>
    <div class="adn ads">
      <div class="gE iv gt">
        <span class="gD" name="${sender}" email="${email}">${sender}</span>
        <span class="g3" title="${date}">Jul 30</span>
      </div>
      <div class="ii gt"><div class="a3s aiL">${body}</div></div>
    </div>`;
}

test("isGmail matches only mail.google.com", () => {
  assert.equal(isGmail({ hostname: "mail.google.com" }), true);
  assert.equal(isGmail({ hostname: "www.google.com" }), false);
});

test("extracts subject, sender, date and body from a single message", () => {
  const article = extractGmail(docFrom(message()));
  assert.equal(article.title, "Quarterly numbers");
  assert.equal(article.byline, "Alice Smith <alice@example.com>");
  assert.equal(article.domain, "mail.google.com");
  assert.match(article.content, /Hello there\./);
  assert.match(article.meta, /Thu, Jul 30, 2026, 9:14 AM/);
  assert.match(article.meta, /Alice Smith/);
});

test("meta uses the date's title attribute, not the relative text", () => {
  const article = extractGmail(docFrom(message()));
  assert.doesNotMatch(article.meta, /^Jul 30 ·/);
});

test("falls back to a cleaned document title when the subject heading is absent", () => {
  const doc = docFrom(
    `<div class="adn ads"><div class="ii gt"><div class="a3s aiL">Body text</div></div></div>`,
    { title: "(3) Re: budget review - me@gmail.com - Gmail" },
  );
  assert.equal(extractGmail(doc).title, "Re: budget review");
});

test("picks the last open message in a multi-message thread", () => {
  const doc = docFrom(`
    ${message({ body: "<div>First message body</div>" })}
    <div class="adn ads">
      <div class="gE iv gt">
        <span class="gD" name="Bob Jones" email="bob@example.com">Bob Jones</span>
        <span class="g3" title="Fri, Jul 31, 2026, 10:00 AM">Jul 31</span>
      </div>
      <div class="ii gt"><div class="a3s aiL"><div>Second message body</div></div></div>
    </div>`);
  const article = extractGmail(doc);
  assert.match(article.content, /Second message body/);
  assert.doesNotMatch(article.content, /First message body/);
  assert.equal(article.byline, "Bob Jones <bob@example.com>");
});

test("a right-clicked message wins over the last-open default", () => {
  const doc = docFrom(`
    ${message({ body: "<div id='first'>First message body</div>" })}
    <div class="adn ads">
      <div class="gE iv gt"><span class="gD" name="Bob" email="bob@example.com">Bob</span></div>
      <div class="ii gt"><div class="a3s aiL"><div>Second message body</div></div></div>
    </div>`);
  const target = doc.getElementById("first");
  const article = extractGmail(doc, target);
  assert.match(article.content, /First message body/);
  assert.doesNotMatch(article.content, /Second message body/);
});

test("right-clicking a message header still resolves to that message's body", () => {
  const doc = docFrom(message({ body: "<div>Only body</div>" }));
  const header = doc.querySelector("span.gD");
  assert.match(extractGmail(doc, header).content, /Only body/);
});

test("drops quoted text when substantial content remains", () => {
  const body = `
    <div>${"Fresh reply content. ".repeat(20)}</div>
    <blockquote class="gmail_quote">Previous message being quoted back</blockquote>`;
  const article = extractGmail(docFrom(message({ body })));
  assert.match(article.content, /Fresh reply content/);
  assert.doesNotMatch(article.content, /Previous message being quoted back/);
});

test("keeps quoted text when it is nearly all of the message", () => {
  const body = `
    <div>Agreed.</div>
    <blockquote class="gmail_quote">The original proposal we are replying to</blockquote>`;
  const article = extractGmail(docFrom(message({ body })));
  assert.match(article.content, /The original proposal we are replying to/);
});

test("strips trim toggles, scripts, styles and auth-gated images", () => {
  const body = `
    <div>Visible text</div>
    <div class="ajR" role="button">...</div>
    <script>tracking()</script>
    <style>.x{color:red}</style>
    <img src="https://mail.google.com/mail/u/0/?view=fimg&amp;id=1">
    <img src="cid:inline-attachment">
    <img src="https://ci3.googleusercontent.com/proxy/abc">`;
  const content = extractGmail(docFrom(message({ body }))).content;
  assert.match(content, /Visible text/);
  assert.doesNotMatch(content, /tracking\(\)/);
  assert.doesNotMatch(content, /color:red/);
  assert.doesNotMatch(content, /mail\.google\.com/);
  assert.doesNotMatch(content, /cid:/);
  // The proxy host is public, so those images should survive.
  assert.match(content, /ci3\.googleusercontent\.com/);
});

test("handles the standalone 'View entire message' layout", () => {
  // ?view=lg has no h2.hP and no .adn wrapper — just the body.
  const doc = docFrom(
    `<div class="a3s aiL"><div>Full message text here</div></div>`,
    { title: "Long newsletter - me@gmail.com - Gmail" },
  );
  const article = extractGmail(doc);
  assert.equal(article.title, "Long newsletter");
  assert.match(article.content, /Full message text here/);
});

test("returns null when there is no message on the page", () => {
  assert.equal(extractGmail(docFrom(`<div class="nH">Inbox list only</div>`)), null);
});

test("returns null for an empty message body so the caller can fall back", () => {
  assert.equal(extractGmail(docFrom(message({ body: "   " }))), null);
});
