# Send to reMarkable

Three ways to get what you are reading onto your reMarkable tablet, as a clean,
tight-margin PDF — no ads, no navigation chrome, 14 pt Georgia, sized for the screen.

| | Component | Where it runs | Needs a server? |
|---|---|---|---|
| **Desktop browser** | [`chrome-extension/`](chrome-extension/) | Your machine | **No** |
| **Android share sheet** | this repo's root (a PWA) | GitHub Pages + your server | **Yes** — [`server/`](server/) |
| **Command line / Claude Code** | [`server/web2pdf.py`](server/web2pdf.py) | Your machine | **No** |

They share nothing but your reMarkable account, so pick whichever you actually want and
ignore the rest. **The Chrome extension is the one most people should start with** — it is
self-contained and takes about five minutes.

---

## Prerequisites

### 1. A reMarkable with cloud sync

Any model. The tablet must be signed in to a reMarkable account with cloud sync on, since
everything here uploads via reMarkable Cloud rather than over USB.

> A paid Connect subscription is **not** required for this. Uploading documents through the
> cloud API works on a free account.

### 2. Google Chrome

Used headlessly to do the actual PDF rendering. Already installed for most people.

### 3. `rmapi` (required for all three)

[`rmapi`](https://github.com/ddvk/rmapi) is a third-party CLI for reMarkable Cloud. It is
what performs the upload.

```bash
# macOS (Apple Silicon) — prebuilt binary
mkdir -p ~/bin
curl -L "https://github.com/ddvk/rmapi/releases/latest/download/rmapi-macos-aarch64.zip" -o /tmp/rmapi.zip
unzip /tmp/rmapi.zip -d ~/bin/ && chmod +x ~/bin/rmapi
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# Linux (x86-64)
curl -sSL https://github.com/ddvk/rmapi/releases/latest/download/rmapi-linux-amd64.tar.gz | tar xz
install -m755 rmapi ~/.local/bin/rmapi
```

Pair it with your account — this is a one-time step:

```bash
rmapi
# Prints a one-time code. Enter it at https://my.remarkable.com/device/desktop/connect
# Credentials are saved to ~/.config/rmapi/rmapi.conf
```

Verify:

```bash
rmapi ls /      # should list your reMarkable folders
```

> **When you later upgrade `rmapi`, keep `~/.config/rmapi/rmapi.conf`.** Deleting it forces
> a re-pair. Upgrading is occasionally necessary: older builds eventually start failing with
> `failed to mirror was not ok: request failed with status 400` as reMarkable changes its API.

---

## 1. Chrome extension (recommended starting point)

One click sends the current tab. Extraction runs **in the browser**, so pages behind a login
work — including Gmail, which gets a dedicated extractor and a right-click "Send this email
to reMarkable", and Google Docs, which is fetched via its HTML export.

> **macOS only as written.** `install.sh` writes Chrome's native-messaging manifest to the
> macOS location, and `native-host/web2pdf_host.py` hardcodes the macOS Chrome path. It
> works on Linux after editing both (the manifest goes in
> `~/.config/google-chrome/NativeMessagingHosts/`, and `CHROME` becomes `google-chrome`),
> but that is not done for you.

```bash
git clone https://github.com/tbuckworth/remarkable-share.git ~/remarkable-share
cd ~/remarkable-share/chrome-extension
npm install && npm run build
```

Then:

1. Open `chrome://extensions` → enable **Developer mode** → **Load unpacked** → select
   `~/remarkable-share/chrome-extension`
2. Copy the extension ID that appears on its card
3. Register the native messaging host: `./install.sh <extension-id>`
4. **Restart Chrome**

Full details, including how the Gmail extraction works and what to do when Google changes
their class names: [`chrome-extension/README.md`](chrome-extension/README.md).

## 2. Android share sheet (PWA)

Share a URL from any Android app → it lands on your reMarkable. Works on mobile data,
anywhere, with **no VPN on the phone**.

This one needs the [`server/`](server/) component running on an always-on machine at home,
because a phone cannot run headless Chrome. Set that up first, then:

1. Open your PWA's URL in Chrome on the phone
2. Three-dot menu → **Install app** / Add to home screen
3. Open it → **Settings** → enter your server URL and auth token
4. From any app: **Share → Send to reMarkable**

> **Note for anyone who is not the author:** the hosted copy at
> <https://tbuckworth.github.io/remarkable-share/> defaults to *the author's* server, which
> will reject your requests. Either override **Server URL** in its Settings screen to point
> at your own, or fork this repo and enable GitHub Pages to host your own copy — in which
> case edit [`tunnel.json`](tunnel.json) to hold your server URL.

### How it fits together

```
┌─────────────┐   share URL    ┌───────────────────────────┐
│ Android app │ ─────────────► │ PWA (GitHub Pages)         │   reads current
│ share sheet │                │ /remarkable-share/         │ ─ server URL from
└─────────────┘                └────────────┬──────────────┘   tunnel.json
                                            │ POST /convert  (HTTPS, Bearer token)
                                            ▼
                         ┌──────────────────────────────────────┐
                         │  Tailscale Funnel (PUBLIC HTTPS)      │  no VPN needed
                         │  https://yourmachine.tailXXXX.ts.net  │  on the phone
                         └────────────────────┬─────────────────┘
                                              ▼  proxy → localhost:8417
                         ┌──────────────────────────────────────┐
                         │  server/web2pdf_server.py             │
                         │  1. fetch URL + Readability extract   │
                         │  2. headless Chrome → clean PDF       │
                         │  3. rmapi upload                      │
                         └────────────────────┬─────────────────┘
                                              ▼
                                    ┌──────────────────────┐
                                    │  reMarkable cloud     │ → syncs to tablet
                                    └──────────────────────┘
```

### Files in the PWA

| File | Purpose |
|------|---------|
| `index.html` | The entire app — UI, share-target handling, server discovery, the `POST /convert` call. Self-contained inline CSS + JS. |
| `manifest.json` | PWA manifest. The `share_target` block is what puts the app in the Android share sheet. |
| `sw.js` | Minimal pass-through service worker. Exists only because a PWA must register one to be installable. |
| `tunnel.json` | The current server URL, read at startup for auto-discovery. |
| `icon-192.png`, `icon-512.png` | Home-screen icons. |

### Server discovery & self-healing

The app resolves which server to talk to in three layers:

- **`FALLBACK_SERVER`** — hard-coded in `index.html`, used when nothing else is set.
- **`tunnel.json`** — fetched at startup; overrides the fallback, but **never** overrides a
  URL you typed in Settings yourself.
- **Settings** — your manual override, stored in `localStorage`.

`isValidServer()` guards all three, rejecting junk and specifically rejecting
`api.trycloudflare.com` (see [History](#history)). An invalid cached URL is cleared and
falls back automatically — that is the "self-healing" part.

## 3. Command line

`server/web2pdf.py` is a plain CLI and needs no server:

```bash
pip install requests readability-lxml beautifulsoup4 lxml
python3 server/web2pdf.py <url> --rm --rm-folder "/Articles"
```

This is also what the `/web2pdf` command in the
[paper-review Claude Code plugin](https://github.com/tbuckworth/claude-remote-setup/tree/main/plugins/paper-review)
wraps, if you use that.

---

## History

Recorded so the current design makes sense.

- **Originally Tailscale `serve` (tailnet-only).** That required the Tailscale VPN running
  on the phone, which was unreliable on mobile data — the phone could not reach its DERP
  relay, so uploads failed with "Failed to fetch". Switched to Tailscale **Funnel**
  (public), so the phone needs no VPN.

- **A `cloudflared` quick-tunnel phase, abandoned.** The desktop ran a Cloudflare *quick*
  tunnel and pushed its rotating `*.trycloudflare.com` URL into `tunnel.json`. Two problems:
  the URL changed on every restart, so there was always a propagation gap; and a bug in the
  publisher script grabbed Cloudflare's **API** host (`api.trycloudflare.com`) instead of the
  tunnel URL and published that. The `isValidServer()` guard is the scar tissue.

- **Funnel's first DNS publish got stuck** — the public record did not appear for several
  minutes. An `off → reset → on` toggle forced it. Worth knowing if it recurs after a reboot.

- **`rmapi` broke two ways at once.** The device pairing had been deleted in an earlier
  debugging session, needing a re-pair; and the old v0.0.32 binary returned `status 400`
  against reMarkable's current API, needing an upgrade. Hence the warning above about
  keeping `rmapi.conf` when upgrading.
