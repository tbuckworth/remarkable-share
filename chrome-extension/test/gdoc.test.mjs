// Fixture mirrors the shape of Docs' HTML export: inline Arial/11pt styling on
// everything, flat lists tagged with a nesting level, redirect-wrapped links,
// and empty spacer paragraphs.

import assert from "node:assert/strict";
import test from "node:test";
import { JSDOM } from "jsdom";

import {
  cleanGoogleDocExport,
  extractGoogleDoc,
  googleDocExportUrl,
  googleDocId,
  googleDocTitle,
  isGoogleDoc,
} from "../src/gdoc.js";

const DOC_URL = "https://docs.google.com/document/d/11diWJONKI_YwvNFQvXNAJcpzIvbgDMeGYgTE9b-W6RY/edit?tab=t.0#heading=h.7olrjtejx3ll";
const EXPORT_URL = "https://docs.google.com/document/d/11diWJONKI_YwvNFQvXNAJcpzIvbgDMeGYgTE9b-W6RY/export?format=html";

const P = 'style="padding:0;margin:0;color:#000000;font-size:11pt;font-family:&quot;Arial&quot;;line-height:1.15;text-align:left"';
const SPAN = 'style="color:#000000;font-weight:400;text-decoration:none;vertical-align:baseline;font-size:11pt;font-family:&quot;Arial&quot;;font-style:normal"';

const EXPORT = `<html><head><meta charset="utf-8"><title>test for remarkable</title>
<style type="text/css">ol.lst-kix_a-0{list-style-type:none}.lst-kix_a-0 > li:before{content:"" counter(lst-ctn-kix_a-0,decimal) ". "}</style>
</head><body class="c9" style="background-color:#ffffff;max-width:468pt;padding:72pt 72pt 72pt 72pt">
<h1 id="h.7olrjtejx3ll" style="padding-top:20pt;font-size:20pt;font-family:&quot;Arial&quot;"><span ${SPAN}>Scoring Rubric</span></h1>
<p ${P}><span ${SPAN}></span></p>
<p ${P}><span ${SPAN}>Plain, </span><span style="font-weight:700;font-size:11pt">bold</span><span style="font-style:italic"> italic</span><span style="vertical-align:super;font-size:8pt">2</span></p>
<p ${P}><span style="color:#1155cc;text-decoration:underline"><a href="https://www.google.com/url?q=https://example.com/a?b%3D1&amp;sa=D" style="color:inherit">link</a></span></p>
<p ${P}><a href="#h.7olrjtejx3ll">TOC</a></p>
<ol class="c4 lst-kix_a-0 start" style="padding:0;margin:0"><li class="c1 li-bullet-0" style="margin-left:36pt">one</li></ol>
<ul class="c4 lst-kix_b-1" style="padding:0;margin:0"><li class="c1" style="margin-left:72pt">nested</li></ul>
<p ${P}><span><img alt="" src="https://lh7-rt.googleusercontent.com/x" style="width:624px;height:300px;margin-left:0" width="624" height="300"></span></p>
</body></html>`;

const parse = (html) => new JSDOM(html, { url: DOC_URL }).window.document;

test("URL helpers", () => {
  assert.equal(isGoogleDoc(new URL(DOC_URL)), true);
  assert.equal(isGoogleDoc(new URL("https://docs.google.com/spreadsheets/d/abc/edit")), false);
  assert.equal(isGoogleDoc(new URL("https://mail.google.com/")), false);
  assert.equal(googleDocId(DOC_URL), "11diWJONKI_YwvNFQvXNAJcpzIvbgDMeGYgTE9b-W6RY");
  assert.equal(googleDocId("https://example.com/document/d/x"), null);
  assert.equal(googleDocExportUrl(DOC_URL), EXPORT_URL);
  assert.equal(googleDocTitle("test for remarkable - Google Docs"), "test for remarkable");
  assert.equal(googleDocTitle("", "fallback"), "fallback");
});

test("cleanGoogleDocExport strips Google styling but keeps emphasis and structure", () => {
  const html = cleanGoogleDocExport(parse(EXPORT));
  const body = parse(`<html><body>${html}</body></html>`).body;

  assert.equal(body.querySelector("style"), null);
  assert.equal(body.querySelector("[class]"), null);
  assert.equal(body.querySelector("h1").id, "h.7olrjtejx3ll");
  assert.equal(body.querySelector("h1").getAttribute("style"), null);
  assert.match(html, /font-weight:700/);
  assert.match(html, /font-style:italic/);
  assert.match(html, /vertical-align:super/);
  assert.doesNotMatch(html, /Arial|font-size|color:#|padding|72pt/);

  const links = [...body.querySelectorAll("a")].map((a) => a.getAttribute("href"));
  assert.deepEqual(links, ["https://example.com/a?b=1", "#h.7olrjtejx3ll"]);

  // Empty spacer paragraph gone; the image paragraph kept.
  const paras = [...body.querySelectorAll("p")];
  assert.equal(paras.length, 4);
  assert.ok(paras.every((p) => p.textContent.trim() || p.querySelector("img")));

  const img = body.querySelector("img");
  assert.equal(img.getAttribute("width"), null);
  assert.equal(img.getAttribute("style"), null);

  assert.equal(body.querySelector("ol").getAttribute("style"), null);
  assert.equal(body.querySelector("ul").getAttribute("style"), "margin-left:1.5em");
  assert.equal(body.querySelector("li").getAttribute("style"), null);
});

test("extractGoogleDoc fetches the export with credentials", async () => {
  let called;
  const fetchFn = async (url, opts) => {
    called = { url, opts };
    return { ok: true, status: 200, text: async () => EXPORT };
  };
  const article = await extractGoogleDoc({ url: DOC_URL, tabTitle: "test for remarkable - Google Docs", fetchFn, parse });
  assert.equal(called.url, EXPORT_URL);
  assert.equal(called.opts.credentials, "include");
  assert.equal(article.title, "test for remarkable");
  assert.equal(article.domain, "docs.google.com");
  assert.match(article.content, /Scoring Rubric/);
});

test("extractGoogleDoc reports a failed export", async () => {
  const fetchFn = async () => ({ ok: false, status: 401, text: async () => "" });
  await assert.rejects(
    extractGoogleDoc({ url: DOC_URL, tabTitle: "x", fetchFn, parse }),
    /HTTP 401/,
  );
});

test("extractGoogleDoc returns null off Docs", async () => {
  assert.equal(await extractGoogleDoc({ url: "https://example.com", tabTitle: "x", fetchFn: async () => {} }), null);
});
