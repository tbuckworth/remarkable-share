# Send to reMarkable — Android share PWA

A tiny installable web app (PWA) that adds **"Send to reMarkable"** to the Android share
sheet. Share any URL from any app → it's rendered to a clean PDF and uploaded to your
reMarkable tablet, from anywhere, on mobile data, **with no VPN**.

- **Live app:** https://tbuckworth.github.io/remarkable-share/
- **This repo** is *only* the phone-facing front end (static files on GitHub Pages).
  The actual work (PDF rendering + upload) happens on a home **desktop server** that
  lives in a different repo — see [Where the server lives](#where-the-server-lives).

---

## How it all fits together

```
┌─────────────┐   share URL    ┌──────────────────────────┐
│ Android app │ ─────────────► │ This PWA (GitHub Pages)   │   reads current
│ share sheet │                │ tbuckworth.github.io/...  │ ─ server URL from
└─────────────┘                └────────────┬─────────────┘   tunnel.json
                                             │ POST /convert  (HTTPS, Bearer token)
                                             ▼
                          ┌──────────────────────────────────────┐
                          │  Tailscale Funnel (PUBLIC HTTPS)       │  no VPN needed
                          │  https://titus-ms-7a59.…ts.net         │  on the phone
                          └────────────────────┬───────────────────┘
                                               ▼  proxy → localhost:8417
                          ┌──────────────────────────────────────┐
                          │  Desktop server: web2pdf_server.py     │
                          │  1. fetch URL + Readability extract    │
                          │  2. headless Chrome → clean PDF        │
                          │  3. rmapi upload                       │
                          └────────────────────┬───────────────────┘
                                               ▼
                                     ┌──────────────────────┐
                                     │  reMarkable cloud     │ → syncs to tablet
                                     └──────────────────────┘
```

### The request flow, step by step

1. **Share** a URL on Android. The `share_target` in [`manifest.json`](manifest.json)
   makes this app appear in the share sheet; Android opens the PWA with the URL as a
   `?url=` (or `?text=`) query param.
2. [`index.html`](index.html) reads that param, pre-fills the URL field, and shows a
   folder picker + Send button.
3. **Server discovery.** On load, the app fetches [`tunnel.json`](tunnel.json) to learn
   the *current* server URL and stores it in `localStorage`. This is the indirection that
   lets the server URL change without re-installing the app. See
   [Server discovery & self-healing](#server-discovery--self-healing).
4. **Send.** The app `POST`s `{url, folder}` to `<server>/convert` with an
   `Authorization: Bearer <token>` header.
5. The **desktop server** fetches the page, extracts the article with Mozilla Readability,
   renders a reMarkable-optimised PDF with headless Chrome, and uploads it with `rmapi`.
6. reMarkable cloud syncs it to the tablet.

---

## Files in this repo

| File | Purpose |
|------|---------|
| [`index.html`](index.html) | The entire app — UI, share-target handling, server discovery, and the `POST /convert` call. Self-contained (inline CSS + JS). |
| [`manifest.json`](manifest.json) | PWA manifest. The `share_target` block is what puts the app in the Android share sheet. |
| [`sw.js`](sw.js) | Minimal service worker (pass-through `fetch`). Exists only because a PWA must register a service worker to be installable. |
| [`tunnel.json`](tunnel.json) | The current server URL, read by the app at startup for auto-discovery. Currently the stable Tailscale Funnel hostname. |
| `icon-192.png`, `icon-512.png` | Home-screen / install icons. |

### Server discovery & self-healing

The app decides which server to talk to using three layers, in `index.html`:

- **`FALLBACK_SERVER`** — a hard-coded stable URL (the Tailscale Funnel hostname). Used
  when nothing else is set.
- **`tunnel.json`** — fetched at startup. If it contains a valid server URL, it overrides
  the fallback (but **never** overrides a URL the user explicitly typed in Settings).
- **User Settings** — an optional manual override stored in `localStorage`.

`isValidServer()` guards all of these: it rejects junk and specifically rejects
`api.trycloudflare.com` (Cloudflare's API host, which an old publisher script sometimes
wrote by mistake — see [History](#history--issues-weve-hit)). If a previously-cached URL
is invalid, the app clears it and falls back — this is the "self-healing" behaviour.

---

## Where the server lives

The front end here is deliberately dumb. Everything server-side lives in the
**`paper-review`** repo and runs on a home desktop (Ubuntu, reachable as `desktop` over
SSH):

| Thing | Location |
|-------|----------|
| Server code | `paper-review/tools/web2pdf_server.py` (FastAPI, port `8417`) |
| Server service | `desktop:/etc/systemd/system/web2pdf-server.service` |
| Public exposure | **Tailscale Funnel** → `https://titus-ms-7a59.tail310336.ts.net` |
| Upload tool | `rmapi` (desktop: `~/.local/bin/rmapi`) → reMarkable cloud |
| Server auth token | `desktop:~/.config/web2pdf/config.json` (must match the token in the PWA Settings) |

Full server docs, the systemd units, and the debugging table are in
**`paper-review/CLAUDE.md`** under "Web-to-reMarkable Tools → Android PWA".

### Public exposure: Tailscale Funnel (not a VPN)

The server is exposed to the public internet with **Tailscale Funnel**. This is the key to
"works on the go": the phone makes an ordinary public HTTPS request — **no Tailscale app or
VPN on the phone**. Only the desktop runs Tailscale.

```bash
# On the desktop — expose local :8417 publicly on :443
sudo tailscale funnel --bg --https=443 http://localhost:8417
sudo tailscale funnel status        # should say "Funnel on"
```

> Funnel must be enabled once for the node in the tailnet admin console (a one-time
> approval link is printed the first time you run the command).

---

## Setup

### Phone (one time)
1. Open https://tbuckworth.github.io/remarkable-share/ in Chrome.
2. Three-dot menu → **Install app** / Add to home screen.
3. Open the app → **Settings** → set the **Auth Token** to match
   `desktop:~/.config/web2pdf/config.json`. (Server URL auto-fills from `tunnel.json`.)
4. From any app, use **Share → Send to reMarkable**.

No VPN required.

### Deploying changes to this app
Push to `main`; GitHub Pages redeploys automatically (allow ~1–2 min + CDN cache). The
live URL stays `https://tbuckworth.github.io/remarkable-share/`.

---

## Operating & debugging

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| "Network error … Failed to fetch" | Funnel not publicly reachable | `ssh desktop "sudo tailscale funnel status"` → expect "Funnel on". Check public DNS: `dig +short @ns1.dnsimple.com titus-ms-7a59.tail310336.ts.net A` should return Tailscale ingress IPs (`176.58.x`). If empty, the DNS publish is stuck — **toggle**: `sudo tailscale funnel --https=443 off && sudo tailscale funnel reset && sudo tailscale funnel --bg --https=443 http://localhost:8417`. |
| 401 Unauthorized | Wrong token | PWA Settings token must match `desktop:~/.config/web2pdf/config.json`. |
| "rmapi upload failed" + server log shows `Enter one-time code` / `Code has the wrong length` | rmapi device token missing/expired | Re-pair: get an 8-char code from https://my.remarkable.com/device/browser/connect, then `echo "<code>" \| ssh desktop 'GODEBUG=netdns=cgo RMAPI_FORCE_SCHEMA_VERSION=4 PATH=$HOME/.local/bin:$PATH rmapi'`. Verify with `rmapi ls /`. |
| "failed to mirror was not ok: request failed with status 400" | rmapi binary too old for reMarkable's current API | Upgrade the binary, **keep the config**: `curl -sSL https://github.com/ddvk/rmapi/releases/download/v0.0.34/rmapi-linux-amd64.tar.gz \| tar xz && install -m755 rmapi ~/.local/bin/rmapi`. |
| App shows old server URL | stale `tunnel.json` cached | Bump `tunnel.json`; GitHub Pages + the phone cache may take a few minutes. The self-heal logic recovers from an invalid cached URL automatically. |

Quick end-to-end test (bypasses the phone, exercises the full public path):
```bash
curl -X POST https://titus-ms-7a59.tail310336.ts.net/convert \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"url":"https://en.wikipedia.org/wiki/ReMarkable","folder":"/"}'
# → {"status":"ok","title":"reMarkable"}
```

---

## History / issues we've hit

Recorded so the next person understands *why* the current design is what it is.

- **Originally used Tailscale `serve` (tailnet-only).** This required the **Tailscale VPN
  to be running on the phone**. On mobile data that was unreliable — the phone couldn't
  reach its DERP relay ("relay server unavailable"), so uploads failed with "Failed to
  fetch". **Fix:** switched to Tailscale **Funnel** (public), so the phone needs no VPN.

- **A `cloudflared` quick-tunnel phase (abandoned).** Before Funnel, the desktop ran a
  Cloudflare *quick* tunnel and pushed its rotating `*.trycloudflare.com` URL into
  `tunnel.json`. Two problems: (1) the URL changed on every restart, so there was always a
  propagation gap; (2) a bug in the publisher script grabbed Cloudflare's **API** host
  (`api.trycloudflare.com`) instead of the tunnel URL and published *that*. The
  `isValidServer()` guard and self-healing logic in `index.html` are the scar tissue from
  this. The `cloudflared-web2pdf.service` on the desktop is now **disabled**.

- **Funnel's first DNS publish got stuck.** After enabling Funnel, the public DNS record
  didn't appear for several minutes (authoritative nameserver returned no record). A
  `funnel off → reset → on` toggle forced it to publish. Worth knowing if it recurs after
  a reboot.

- **rmapi broke in two ways at once.** The device pairing config had been deleted in an
  earlier debugging session (never re-registered) → had to re-pair with a one-time code.
  Then the old **v0.0.32** binary returned `status 400` against reMarkable's current API →
  upgraded to **v0.0.34**. When upgrading rmapi, **keep** `~/.config/rmapi/rmapi.conf` —
  deleting it forces a re-pair.
