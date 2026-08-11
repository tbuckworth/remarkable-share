# Send to reMarkable — desktop server

The backend the [Android PWA](../README.md) posts to. Accepts `{url, folder}` over HTTPS,
renders a clean PDF with headless Chrome, and uploads it to reMarkable Cloud with `rmapi`.

You only need this if you want the **Android** path. The [Chrome extension](../chrome-extension/)
does its own rendering locally and needs no server at all.

| File | |
|---|---|
| `web2pdf.py` | The conversion pipeline: fetch → Mozilla Readability → headless Chrome → PDF → `rmapi`. Also usable standalone as a CLI. |
| `web2pdf_server.py` | FastAPI wrapper around it. Listens on port `8417`. |
| `web2pdf-server.service.example` | systemd unit template — edit the paths, then install. |

## What you need

- An **always-on machine** (Linux or macOS). This is the one real cost of the Android path:
  the phone needs something to talk to, and that something has to be awake.
- **Google Chrome** installed (used headlessly to render the PDF).
- **`rmapi`**, installed and paired with your reMarkable account — see the
  [root README](../README.md#3-rmapi-required-for-all-three) .
- **Tailscale** on that machine (not on the phone).

## Install

```bash
git clone https://github.com/tbuckworth/remarkable-share.git ~/remarkable-share
cd ~/remarkable-share/server

python3 -m venv ~/web2pdf-env
~/web2pdf-env/bin/pip install fastapi uvicorn requests readability-lxml beautifulsoup4 lxml
```

Start it once by hand to generate your auth token:

```bash
~/web2pdf-env/bin/python3 web2pdf_server.py
```

On first run it creates `~/.config/web2pdf/config.json` with a freshly generated random
token and prints it. **That token is the only thing protecting your server** once it is
publicly exposed — treat it like a password, and put it in the PWA's Settings screen rather
than into any file you might commit.

Confirm it works locally, then stop it with Ctrl-C:

```bash
curl localhost:8417/health     # -> {"status":"ok"}
```

## Run it as a service (Linux)

```bash
sed -e "s/YOURUSER/$USER/g" web2pdf-server.service.example \
  | sudo tee /etc/systemd/system/web2pdf-server.service

sudo systemctl daemon-reload
sudo systemctl enable --now web2pdf-server
systemctl status web2pdf-server
```

> The unit sets `GODEBUG=netdns=cgo`. Keep it. Without it, `rmapi` hangs on Linux because
> of a Go pure-resolver DNS bug — the server appears to accept the request and then never
> finishes.

## Expose it to the phone

Use **Tailscale Funnel**, which makes the server reachable over ordinary public HTTPS. This
is what lets the phone work on mobile data with no VPN app installed:

```bash
sudo tailscale funnel --bg --https=443 http://localhost:8417
sudo tailscale funnel status        # expect "Funnel on"
```

The first run prints a one-time approval link — Funnel has to be enabled for the node in
your tailnet's admin console. Your public hostname will look like
`https://yourmachine.tailXXXXXX.ts.net`; that is what goes in the PWA's Settings.

The config persists across reboots. Verify the whole public path end to end:

```bash
curl -X POST https://yourmachine.tailXXXXXX.ts.net/convert \
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"url":"https://en.wikipedia.org/wiki/ReMarkable","folder":"/"}'
# -> {"status":"ok","title":"reMarkable"}
```

## Using `web2pdf.py` on its own

No server needed — it is a normal CLI:

```bash
pip install requests readability-lxml beautifulsoup4 lxml
python3 web2pdf.py <url> --rm --rm-folder "/Articles" --font-size 14pt
```

## Debugging

| Symptom | Cause | Fix |
|---|---|---|
| Phone: "Network error / Failed to fetch" | Funnel not publicly reachable | `sudo tailscale funnel status` should say "Funnel on". Check public DNS resolves: `dig +short yourmachine.tailXXXXXX.ts.net A`. If empty, the DNS publish is stuck — toggle it: `sudo tailscale funnel --https=443 off && sudo tailscale funnel reset && sudo tailscale funnel --bg --https=443 http://localhost:8417` |
| Phone: "Network error" but server is up | Mixed content | The server URL in Settings must be `https://…ts.net`, never a plain `http://` LAN address. Browsers block HTTPS→HTTP. |
| `401 Unauthorized` | Token mismatch | The PWA Settings token must equal `token` in `~/.config/web2pdf/config.json`. |
| `rmapi upload failed`, log shows `Enter one-time code` | Device token missing or expired | Re-pair: get an 8-character code from <https://my.remarkable.com/device/browser/connect>, then run `rmapi` and paste it. |
| `failed to mirror was not ok: request failed with status 400` | `rmapi` too old for reMarkable's current API | Upgrade the binary but **keep** `~/.config/rmapi/rmapi.conf`, or you will have to re-pair. |
| `rmapi` hangs, no error | Go DNS resolver bug on Linux | Ensure `GODEBUG=netdns=cgo` is in the systemd unit's `Environment`. |
| First conversion after a reboot times out | Cold Chrome start at 2.35× render scale | Expected — roughly 30 s. Later renders are fast. The timeout is 60 s. |

```bash
journalctl -u web2pdf-server -n 50      # logs
sudo systemctl restart web2pdf-server   # restart
```
