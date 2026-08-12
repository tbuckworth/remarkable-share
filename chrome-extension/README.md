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
| Toolbar icon (or ⌘⇧E / Ctrl+Shift+E) | The current tab, with a folder picker |
| Right-click → "Send this email to reMarkable" | On Gmail only: the message under the cursor, to the last-used folder |

Both go through `src/send.js`.

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

## Development

```bash
npm install
npm run build      # src/ -> dist/  (Chrome loads dist/, not src/)
npm run watch      # rebuild on change
npm test           # jsdom tests for the Gmail extractor
```

**Editing `src/` does nothing until you build**, and after building you must hit reload on
the extension in `chrome://extensions`. Manifest changes (permissions, content scripts)
need the reload too; adding a content script does not apply to already-open tabs, so
reload Gmail as well.

## Install

1. `npm install && npm run build`
2. `chrome://extensions` → Developer mode → **Load unpacked** → this directory
3. `./install.sh <extension-id>` — registers the native messaging host (copy the ID from
   the extensions page)
4. Restart Chrome

Requires a working `rmapi` on the machine (`rmapi ls /` should list your reMarkable).

## Layout

| Path | |
|---|---|
| `manifest.json` | MV3 manifest; this directory is what's loaded unpacked |
| `src/gmail.js` | Gmail message extraction |
| `src/content.js` | Runs in the page: Gmail extractor, else Readability + math preservation |
| `src/send.js` | Shared extract → upload pipeline |
| `src/popup.js` | Toolbar popup: folder list, send button, local-PDF passthrough |
| `src/background.js` | Service worker: context menu, badge feedback |
| `native-host/web2pdf_host.py` | Renders HTML → PDF with headless Chrome, uploads via `rmapi` |
| `test/gmail.test.mjs` | jsdom tests over Gmail-shaped fixtures |
| `dist/` | Build output — what Chrome actually runs |

## Notes

- Local PDFs (`file://`) are read in the browser and passed as base64: macOS TCC blocks the
  native host from reading `~/Downloads` directly. Needs "Allow access to file URLs"
  enabled for the extension.
- arXiv PDF URLs are named from the paper title returned by arXiv's metadata API. If that
  lookup is unavailable, the extension falls back to the PDF URL's filename.
- Duplicate document names get ` (v2)`, ` (v3)` suffixes rather than overwriting.
