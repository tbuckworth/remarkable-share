# Send to reMarkable — Chrome extension

Sends the page you're reading to your reMarkable as a clean PDF. Extraction runs in the
browser (so authenticated pages work), rendering and upload happen locally via a native
messaging host that shells out to `rmapi`.

This is the **desktop** path, and it is fully self-contained — nothing here talks to a
server. The Android path is the PWA at the root of this repo, which posts a URL to the
`server/` component instead; the two share nothing but the reMarkable account.

## Ways to send

| | What it sends |
|---|---|
| Toolbar icon (or ⌘⇧E / Ctrl+Shift+E) | The current tab, with a folder picker, an optional "Why am I saving this?" note and a mode |
| Right-click → "Send this email to reMarkable" | On Gmail only: the message under the cursor, to the last-used folder (no note, mode Auto) |

Both go through `src/send.js`.

## Why note and mode

The popup has a one-line **"Why am I saving this?"** box (focused when the popup opens;
Enter sends) and an **Auto / Work / Personal** choice, default Auto. Both are optional and
neither is remembered between sends. The context menu has no UI, so it sends an empty note
and `auto`.

Every upload carries them to the native host along with the source URL and the send time,
and the host writes them into the PDF's document-info dictionary so a job reading the
reMarkable cloud copy can recover them:

| Key | Value |
|---|---|
| `/Web2pdfNote` | The note, whitespace collapsed to one line, at most 500 chars; `""` when empty |
| `/Web2pdfMode` | `auto`, `work` or `personal` (anything else becomes `auto`) |
| `/Web2pdfUrl` | The page the send came from (the PDF URL, or `file://` path, for direct PDFs) |
| `/Web2pdfSentAt` | Click time, ISO 8601 with a numeric offset, e.g. `2026-10-29T11:09:05+00:00` |
| `/Subject` | The note, only when non-empty (an existing `/Subject` is otherwise left alone) |

Other entries, `/Title` included, are kept as they were. For rendered articles `/Title` is
whatever headless Chrome writes, which is the temporary HTML file's name, so don't read the
title from there. Rendered articles also print `Why: <note>` under the title on page 1.
Direct PDF uploads get the metadata only: a cover page would shift every page number.

Writing metadata needs [`pypdf`](https://pypi.org/project/pypdf/) importable by the
interpreter the host runs under (its `#!/usr/bin/env python3`, which under Chrome resolves
to `/usr/bin/python3` on macOS). Without it, or for a PDF pypdf can't rewrite (encrypted,
malformed), the host logs a warning to stderr, uploads the PDF unchanged, and the popup
shows the warning after "Sent: ...". `/usr/bin/python3 -m pip install --user pypdf` adds it.

## Gmail

Readability can't extract from Gmail — on a thread view it sees the whole SPA and finds no
single article. `src/gmail.js` handles Gmail directly instead: it locates the open message
body (`div.a3s`), walks up to its container for sender and date, and strips quoted text,
trim-toggles and inline images whose URLs need auth (headless Chrome can't fetch those, so
they'd render as broken boxes). It falls back to Readability if anything is missing.

The **context menu exists because Chrome doesn't render the toolbar in popup windows** —
Gmail's "open in new window" pop-out has no extension icon, and no setting changes that.
Right-click works there.

In a thread with several messages open, the toolbar icon sends the *last* one (Gmail
auto-expands the newest). Right-click the message you want to be unambiguous.

Gmail's class names are obfuscated and Google can change them. If extraction starts
returning the wrong thing, the selectors in `src/gmail.js` are the place to look; the tests
in `test/gmail.test.mjs` encode the DOM shape they expect.

## Google Docs

Readability can't extract from Google Docs either: the editor paints its text onto a
canvas, so the DOM holds nothing readable and the result is garbage. `src/gdoc.js` instead
fetches the document's HTML export (`/document/d/<id>/export?format=html`) from inside the
tab, so your own login is used and private docs work. Google's styling (Arial 11 pt,
colours, page padding, fixed image sizes) is stripped back to bold/italic/underline and
super/subscript, and the result goes through the normal 14 pt Georgia render.

Limits: Docs expresses nested lists as sibling lists, so nesting shows as indentation
rather than true sub-lists, and the table of contents loses its indentation. Images are
fetched by headless Chrome from Google's signed URLs at render time.

## Development

```bash
npm install
npm run build      # src/ -> dist/  (Chrome loads dist/, not src/)
npm run watch      # rebuild on change
npm test           # node:test + jsdom (Gmail, Docs, popup, send) and the host's unittest suite
```

The jsdom tests need Node 20.19 or newer (jsdom 29). The host tests that need pypdf skip
without it; the Chrome render test also needs a local Chrome.

**Editing `src/` does nothing until you build**, and after building you must hit reload on
the extension in `chrome://extensions`. Manifest changes (permissions, content scripts)
need the reload too; adding a content script does not apply to already-open tabs, so
reload Gmail as well.

## Install

1. `npm install && npm run build`
2. `chrome://extensions` → Developer mode → **Load unpacked** → this directory
3. `./install.sh` — registers the native messaging host
4. Restart Chrome

Requires a working `rmapi` on the machine (`rmapi ls /` should list your reMarkable), and
`pypdf` for the why-note metadata (see [Why note and mode](#why-note-and-mode)).

### `~/bin/rmapi` is a patched build — don't replace it with a stock release

From 2026-08-17 the reMarkable cloud began rejecting any write whose root index
isn't sorted by document ID, with `400 {"message":"invalid root schema"}`. Reads keep
working, so the symptom is `rmapi ls` fine but every `put`/`mkdir` failing, and the
extension surfacing it as `rmapi failed: ... status 400`. Nothing local causes it and
no released rmapi fixes it — v0.0.34 and master are both affected
([ddvk/rmapi#75](https://github.com/ddvk/rmapi/issues/75),
[#76](https://github.com/ddvk/rmapi/issues/76)).

`~/bin/rmapi` is therefore built from `ddvk/rmapi` master with
[PR #77](https://github.com/ddvk/rmapi/pull/77) applied, which sorts the index at the
two serialization points. The stock binary it replaced is kept at `~/bin/rmapi.bak`.

Once PR #77 is merged and released, a normal install is fine again. Until then,
`brew`/release upgrades will silently reintroduce the bug.

### Moving this directory changes the extension ID

For an unpacked extension Chrome derives the ID from the **absolute install path**. Moving
or renaming this directory therefore changes the ID: the native messaging host no longer
recognises the extension, and the old entry in `chrome://extensions` points at a directory
that doesn't exist, so clicking the toolbar icon gives `ERR_FILE_NOT_FOUND`. That is exactly
what happened when the extension moved here out of `paper-review`.

**After any move:** remove the stale entry, **Load unpacked** from the new location, run
`./install.sh`, restart Chrome. `install.sh` computes the ID the same way Chrome does, so it
needs no argument and can't drift out of sync with what the extensions page shows.

**Watch which path you pick in the file dialog.** `~/pyg` is also reachable as `/Volumes/pyg`,
an SMB mount of the *desktop's* copy of the same repos. Loading through that path makes it a
different extension as far as Chrome is concerned (hence "Access to the specified native
messaging host is forbidden"), runs the desktop's checkout against the local native host, and
breaks whenever the share is unmounted. Prefer `/Users/titus/...` — ⌘⇧G in the picker lets you
type a path. `install.sh` accepts several IDs (`./install.sh <id> <id>`) if you really do want
both routes registered.

This can be made permanent by adding a `key` (a DER-encoded RSA **public** key, base64) to
`manifest.json` — Chrome then derives the ID from the key instead of the path, and moving
the directory costs nothing. `install.sh` already prefers a `key` when one is present.
Committing that blob trips the global gitleaks pre-commit hook, which reads it as a
high-entropy generic secret; it would need an allowlist entry in `~/.gitleaks.toml` first.

## Layout

| Path | |
|---|---|
| `manifest.json` | MV3 manifest; this directory is what's loaded unpacked |
| `src/gmail.js` | Gmail message extraction |
| `src/content.js` | Runs in the page: Gmail extractor, else Readability + math preservation |
| `src/send.js` | Shared extract → upload pipeline; adds note, mode, source URL, send time |
| `src/popup.js` | Toolbar popup: folder list, why-note and mode, send button, local-PDF passthrough |
| `src/background.js` | Service worker: context menu, badge feedback |
| `native-host/web2pdf_host.py` | Renders HTML → PDF with headless Chrome, writes the why-note metadata, uploads via `rmapi` |
| `test/gmail.test.mjs` | jsdom tests over Gmail-shaped fixtures |
| `test/send.test.mjs`, `test/popup.test.mjs` | Every upload message carries the why-note fields; the popup wires them up |
| `test/test_web2pdf_host.py` | Host: arXiv titles, metadata written and read back, `Why:` line, degradation without pypdf |
| `dist/` | Build output — what Chrome actually runs |

## Notes

- Local PDFs (`file://`) are read in the browser and passed as base64: macOS TCC blocks the
  native host from reading `~/Downloads` directly. Needs "Allow access to file URLs" enabled
  on the extension's Details page. That toggle only appears because of the `file:///*` entry
  in `host_permissions` — Chrome hides it from extensions that couldn't act on file URLs
  anyway, so without it the popup's `fetch()` fails with "Not allowed to load local resource"
  and there is no switch to flip.
- arXiv PDF URLs are named from the paper title returned by arXiv's metadata API. If that
  lookup is unavailable, the extension falls back to the PDF URL's filename.
- Duplicate document names get ` (v2)`, ` (v3)` suffixes rather than overwriting.
