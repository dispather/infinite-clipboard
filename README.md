# Infinite Clipboard

<p align="center">
  <img src="assets/generated/icon-512.png" width="120" alt="Infinite Clipboard icon">
</p>

English | **[한국어](README.ko.md)**

[![GitHub release](https://img.shields.io/github/v/release/dispather/infinite-clipboard)](https://github.com/dispather/infinite-clipboard/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**One clipboard, every PC.**

Stop AirDropping to yourself, emailing files to your own inbox, or pasting into a Slack DM just to move something between your own computers. Infinite Clipboard keeps every PC you own — Windows, macOS, Linux — in sync automatically, over your own Tailscale network.

- 📋 **Instant text sync** — copy on one PC, it's already on the clipboard of every other connected PC
- 📁 **On-demand file & folder transfer** — copying just makes a file available; it only transfers when you actually paste it elsewhere, so idle PCs never get clogged with data they don't need. Interrupted transfers resume automatically
- 🖼️ **Images sync too** — screenshots and copied images work the same way
- 🔒 **Private by design** — travels over your own Tailscale (WireGuard) network, gated by a shared key — no cloud service sits in the middle
- 🖥️ **Lives in your tray** — set it up once on each PC, forget it's running

<p align="center">
  <img src="assets/screenshots/transfers.png" alt="Receive a file with one click" width="480">
</p>

---

## Install

Download the package for your OS from the [GitHub Releases](https://github.com/dispather/infinite-clipboard/releases) page. Every PC's `auth_key` **must match exactly** (see "Share the key once" below).

| OS | File (X.Y.Z = version) | Install |
|----|------------------------|---------|
| Linux (Arch/CachyOS) | `infinite-clipboard-X.Y.Z-1-x86_64.pkg.tar.zst` | `sudo pacman -U <file>` |
| macOS (Apple Silicon, M-series) | `Infinite.Clipboard.X.Y.Z-apple-silicon.dmg` | Open the DMG → drag to `/Applications` → **Gatekeeper bypass required, see below** |
| macOS (Intel) | `Infinite.Clipboard.X.Y.Z-intel.dmg` | Same as above |
| Windows | `infinite-clipboard-setup-X.Y.Z.exe` | Run the installer and follow the prompts (per-user, no admin rights needed) |

After installing:
- Linux: Application menu → Infinite Clipboard
- macOS: Launchpad, or `open "/Applications/Infinite Clipboard.app"`. Run the Gatekeeper bypass below first or it won't launch
- Windows: Start menu → Infinite Clipboard (you can check "start automatically" during install)

### macOS Gatekeeper bypass (one-time)

The DMG isn't code-signed, so macOS quarantines it ("Apple could not verify this app is free of malware..."). Clear the quarantine flag once with:

```sh
xattr -dr com.apple.quarantine "/Applications/Infinite Clipboard.app"
```

After that it runs and auto-starts like any normal app. You'll need to re-run this command once whenever you overwrite it with a new version.

> Right-click → Open doesn't reliably work around the block on unsigned builds (Apple Silicon or Intel) — the `xattr` command is the reliable fix.

### Share the auth key once

On first launch, each PC generates its own random `auth_key`. For PCs to connect to each other, they all need the same key.

```
1. Launch the app on PC A → auth_key is generated in that PC's settings.json
2. Copy PC A's auth_key value
3. Paste the same value into auth_key in the other PC's settings.json + restart the app
   (or type it directly into Tray → Settings → Auth Key)
```

Settings file locations:

| OS | Path |
|----|------|
| Linux | `~/.config/InfiniteClipboard/settings.json` |
| macOS | `~/Library/Application Support/InfiniteClipboard/settings.json` |
| Windows | `%APPDATA%\InfiniteClipboard\settings.json` |

---

## Usage

- **Text sharing**: Ctrl+C on any PC → syncs instantly to every connected PC, no extra steps
- **File/image transfer (lazy)**: copying a file/folder/image just notifies the other PCs that something is available — the actual transfer only starts when you **press Ctrl+V on that PC, or click "Receive" in the tray's Transfers window** (copying alone never auto-transfers). Once complete, it's saved to `~/Downloads/` (or your configured path)
- **Settings**: right-click the tray icon → Settings
- **History**: right-click the tray icon → Clipboard History
- **Transfer progress / receiving**: right-click the tray icon → Transfers
- **Autostart**: toggle "Start automatically" in the settings window
- **Updates** (v3.0.14+): the app checks GitHub for a new release 30 seconds after it starts and then once a day. When one is out, the tray menu shows **Install Update (vX.Y.Z)** — one click downloads it, verifies its SHA-256 checksum, installs it, and restarts the app. You can also use **Check for Updates** in the tray menu at any time, or turn off the automatic check in the settings window

<p align="center">
  <img src="assets/screenshots/settings.png" alt="Settings window" width="300">
  &nbsp;&nbsp;
  <img src="assets/screenshots/history.png" alt="Clipboard history window" width="380">
</p>

### Tray icon colors

| Color | Meaning |
|-------|---------|
| Green | Server: a client is connected / Client: connected to server |
| Yellow | Server waiting (no clients connected) |
| Red | Client disconnected |
| Gray | Initial state |

---

## Network setup

```
[Server PC — always on]            [Client PCs]
  Tailscale IP: 100.64.0.1         Tailscale IP: 100.64.0.x
       │                                  │
       └──────── Tailscale VPN ────────────┘
                    (WireGuard encryption)
```

- One fixed server PC, the rest are clients
- Tailscale's WireGuard handles encryption — there's no additional app-level encryption
- Clients auto-reconnect if the server restarts (5s interval by default)
- Default port `9999`

### Firewall

Works out of the box in most setups if you're only using Tailscale. For a direct LAN connection:

```bash
# Linux (ufw)
sudo ufw allow 9999/tcp

# Windows PowerShell (as Administrator)
New-NetFirewallRule -DisplayName "Infinite Clipboard" -Direction Inbound -Protocol TCP -LocalPort 9999 -Action Allow

# macOS
# System Settings → Network → Firewall → allow InfiniteClipboard
```

---

## Troubleshooting

### "Unidentified developer" warning on first macOS launch
Gatekeeper is blocking the ad-hoc signed app. Right-click the `.app` in Finder → Open, once, and it'll launch normally after that.

### Can't connect — "authentication failed"
Make sure every PC's `auth_key` matches exactly — a single character off will fail. The most reliable fix: copy the server PC's `settings.json` and overwrite it on the client PCs.

### File transfer stalls partway through
Automatic resume is built in. Restart the app and it'll pick up from the checkpoint (`~/.config/InfiniteClipboard/checkpoints/` or the equivalent settings folder per OS). If resume also fails, manually delete the temp directory (e.g. `/tmp/ic_transfer_<id>/`) and retry the transfer.

### SmartScreen warning on the Windows installer
Happens because there's no proper code-signing certificate. Click "More info" → "Run anyway". If you need code signing, buy a certificate and sign with `signtool`.

### Updating
From **v3.0.14** on, update from the tray menu (**Install Update (vX.Y.Z)**, or **Check for Updates** first). **If you're on v3.0.13 or older, install v3.0.14 manually once** — the updater ships inside the app, so older versions don't have it.

- **Windows**: installs silently and relaunches the app. If you installed "for all users" (Program Files), the installer opens normally instead so Windows can ask for permission
- **Linux (Arch package)**: a system password prompt appears once (the package is installed with `pacman -U`). If you cancel, the current version simply starts again
- **macOS**: the app in `/Applications` is replaced and relaunched — no quarantine command needed for updates
- The update is refused while a file transfer is in progress. Files waiting to be received stay in the Transfers window after the restart (v3.0.17+)
- Running from source, or installed somewhere the app can't write to? The menu item opens the release page instead
- The installer's log is `update-helper.log` next to the app's log file; after restarting, the app tells you whether the update was installed

Check the installed version:
```bash
infinite-clipboard --version
```

---

## Building from source / Contributing

Want to build a native package yourself, run in developer mode, or contribute code? See **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## Support

If Infinite Clipboard saves you some copy-pasting hassle, consider buying me a coffee — it helps keep this maintained.

[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/dispather)

## License

MIT License — see the `LICENSE` file for details.
