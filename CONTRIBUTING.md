# Contributing to Infinite Clipboard

English | **[한국어](CONTRIBUTING.ko.md)**

This doc covers building native packages from source, running in developer mode, and code contribution guidelines. If you just want to install and use the app, see the [README](README.md) instead.

---

## Build guide

How to produce a native package for each OS. Use this when you've changed the source and need to redistribute, or want to install onto a new PC.

### Common prerequisites

- Python 3.10 or newer
- A git-cloned project folder, or a cloud-synced folder
- Internet access (to download dependencies on the first build)

### Common workflow

```
┌─────────────────────────┐
│ assets/icon.svg (source) │
└───────────┬─────────────┘
            │ generate_icons.sh
            ▼
┌─────────────────────────┐
│ assets/generated/        │  ← shared across OSes; generate once and sync/commit
│   tray-{green|amber|..}  │
│   icon.png / ico / icns  │
└───────────┬─────────────┘
            │ build_{linux|mac|win}
            ▼
┌─────────────────────────┐
│ dist/InfiniteClipboard   │  ← PyInstaller onedir bundle
└───────────┬─────────────┘
            │ make_dmg / makepkg / iscc
            ▼
┌─────────────────────────┐
│ Installable package      │
│  .pkg.tar.zst / .dmg /   │
│  Setup.exe                │
└─────────────────────────┘
```

---

### 0. Automated builds (GitHub Actions) — recommended

Builds all **3 OS packages simultaneously** on cloud runners, no local machine needed.
Defined in `.github/workflows/build.yml`.

**Triggers**

| Method | Effect |
|--------|--------|
| `git tag vX.Y.Z && git push origin vX.Y.Z` | Builds all 3 OSes + creates a **draft** GitHub Release with the installer/.pkg/.dmg attached |
| GitHub Actions web UI → "Run workflow" | Build only (artifacts kept, no Release created) |

**Artifacts**

- Windows: `infinite-clipboard-setup-X.Y.Z.exe` (Inno Setup per-user installer)
- Linux: `infinite-clipboard-X.Y.Z-1-x86_64.pkg.tar.zst` (Arch/CachyOS pacman package)
- macOS: `Infinite Clipboard X.Y.Z.dmg` (Apple Silicon ARM64, drag-to-install)

**Release procedure**

1. Sync the version in `version.py` and `pyproject.toml`, then commit
2. `git tag vX.Y.Z && git push origin vX.Y.Z`
3. Confirm all 3 OS builds finish in the Actions tab (Windows/Linux ~1-2 min each, macOS 15-20 min on first build / ~5 min once cached)
4. Review the changelog on the draft Release page → **Publish**

**Constraints**

- **No macOS code signing** → Gatekeeper blocks the downloaded DMG. Users need a one-time manual bypass:
  ```bash
  # after dragging to /Applications/Infinite\ Clipboard.app
  xattr -dr com.apple.quarantine "/Applications/Infinite Clipboard.app"
  ```
  A proper fix requires enrolling in the Apple Developer Program ($99/yr) and adding signing + notarization steps to the workflow (tracked separately)
- macOS builds cover both **Apple Silicon (ARM64, `macos-15`)** and **Intel (x86_64, `macos-15-intel`)** as of 2026-07-11

---

### 1. Generate icon assets (once, or whenever `assets/icon.svg` changes)

Converts the icon SVG into 4 state-colored (green/amber/red/gray) PNGs plus `.ico` (Windows), `.icns` (macOS), and `icon-512.png` (Linux).

```bash
# Dependencies (install once, on Linux or macOS)
#   Arch/CachyOS:  sudo pacman -S librsvg imagemagick
#   macOS:         brew install librsvg imagemagick
#   Debian/Ubuntu: sudo apt install librsvg2-bin imagemagick

./build/generate_icons.sh
```

Output goes to `assets/generated/`, which is **committed to git**, so other PCs (e.g. Windows) can build directly without re-generating anything. You don't need to run this script on Windows at all — it just uses the already-generated files.

If the tools are missing, it guides you automatically:
```
❌ 'rsvg-convert' command not found.
   Arch/CachyOS:  sudo pacman -S librsvg
   Debian/Ubuntu: sudo apt install librsvg2-bin
   macOS:         brew install librsvg
```

---

### 2. Linux build (CachyOS/Arch)

#### (a) Build the PyInstaller onedir bundle

```bash
# System dependencies (once)
sudo pacman -S python-gobject libayatana-appindicator wl-clipboard xclip

# Run the build script
chmod +x build/build_linux.sh
./build/build_linux.sh
```

What it does:
1. Runs `generate_icons.sh` automatically if `assets/generated/icon-512.png` is missing
2. Creates `.venv` with `--system-site-packages` if it doesn't exist yet (needed for AppIndicator access)
3. Installs `requirements_linux.txt`
4. Installs PyInstaller 6.0+
5. Builds using `build/infinite-clipboard.spec`

Result: `dist/InfiniteClipboard/InfiniteClipboard` (executable, ~129MB bundle)

Try it right away:
```bash
./dist/InfiniteClipboard/InfiniteClipboard --no-tray --debug
```

#### (b) Build the pacman package (.pkg.tar.zst)

```bash
cd build
makepkg -f          # reads PKGBUILD to produce the package
```

Result: `build/infinite-clipboard-X.Y.Z-1-x86_64.pkg.tar.zst`

Install/remove:
```bash
sudo pacman -U infinite-clipboard-X.Y.Z-1-x86_64.pkg.tar.zst
sudo pacman -R infinite-clipboard
```

Package contents:
- `/opt/infinite-clipboard/` — executable + `_internal/` runtime
- `/usr/bin/infinite-clipboard` — symlink for running from a terminal
- `/usr/share/applications/infinite-clipboard.desktop` — application menu entry
- `/usr/share/icons/hicolor/512x512/apps/infinite-clipboard.png`
- `/usr/share/licenses/infinite-clipboard/LICENSE`

Dependencies (`libayatana-appindicator`, `python-gobject`, `wl-clipboard`, `xclip`) are installed automatically by pacman.

---

### 3. macOS build

#### (a) Build the `.app` bundle

```bash
# System dependencies (once)
brew install librsvg imagemagick

# Build
chmod +x build/build_mac.sh
./build/build_mac.sh
```

What it does:
1. Verifies icon assets exist (auto-generates `.icns` if missing)
2. Creates `.venv` if missing + installs `requirements_mac.txt`
3. Builds the `.app` bundle with PyInstaller
4. Ad-hoc signs it with `codesign --force --deep --sign -`

Result: `dist/InfiniteClipboard.app`

Properties:
- `LSUIElement=1` — menu bar only, no Dock icon
- `console=False` — no terminal window
- `bundle_identifier=com.dispather.infinite-clipboard`
- `CFBundleVersion=X.Y.Z`

Run it directly:
```bash
open dist/InfiniteClipboard.app
```

If Gatekeeper warns on first launch, right-click the `.app` in Finder → Open, once.

#### (b) Package the DMG

```bash
chmod +x build/make_dmg.sh
./build/make_dmg.sh
```

Result: `dist/InfiniteClipboard-X.Y.Z.dmg`

The DMG includes an `/Applications` symlink for the standard drag-to-install UX.

---

### 4. Windows build

#### (a) PyInstaller onedir bundle

```cmd
:: Prerequisite: Python 3.10+ installed (on PATH), assets\generated\ already synced
build\build_win.bat
```

What it does:
1. Verifies `assets\generated\icon.ico` exists (aborts with an error if missing — generate it on Linux/macOS first)
2. Creates `.venv` if missing + installs `requirements_win.txt`
3. Installs PyInstaller
4. Builds using the shared spec file

Result: `dist\InfiniteClipboard\InfiniteClipboard.exe`

#### (b) Inno Setup installer

1. **Install Inno Setup**: https://jrsoftware.org/isdl.php (free)
2. Add `iscc` to PATH, or use the Inno Setup Compiler GUI
3. Compile:

```cmd
iscc build\installer.iss
```

Result: `build\Output\InfiniteClipboard-Setup-X.Y.Z.exe`

Installer features:
- Installs to Program Files (falls back to `%LOCALAPPDATA%` if UAC is declined)
- Start menu shortcut
- Optional checkboxes: desktop shortcut / **start automatically with Windows**
- Uninstall page lets you keep or remove settings
- English + Korean

---

### 5. Release update checklist

Places that need manual sync when bumping the version:

| File | Value |
|------|-------|
| `version.py` | `__version__ = "x.y.z"` |
| `pyproject.toml` | `version = "x.y.z"` |
| `build/installer.iss` | `#define AppVersion "x.y.z"` |
| `build/PKGBUILD` | `pkgver=x.y.z` |
| `build/infinite-clipboard.spec` | `"CFBundleShortVersionString": "x.y.z"`, `"CFBundleVersion": "x.y.z"`, `version="x.y.z"` |

Once these are all updated, just re-run the build script on each OS.

---

## Developer mode (run without building, via `uv run`)

For iterating on the source without producing a distributable build.

### 1. Install uv (once per PC)

```bash
# Arch/CachyOS
sudo pacman -S uv

# macOS
brew install uv

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Run it

```bash
# from the repo root

# First run pops up a GUI setup dialog (mode / server IP / port)
python3 start.py

# or pass CLI options directly
uv run main.py --mode server
uv run main.py --mode client --host 100.64.0.1
```

> On Linux with a Wayland session: `sudo pacman -S wl-clipboard python-gobject libayatana-appindicator`
> On an X11 session: `sudo pacman -S xclip python-gobject libayatana-appindicator`

### 3. Sharing via a synced folder

Syncing the project folder with Syncthing/Nextcloud etc. lets every PC run the same code.

**Exclude from sync:**
```
__pycache__/
.venv/
dist/
build/InfiniteClipboard/
build/Output/
```

`assets/generated/` should **stay in sync** — so every PC can build without needing rsvg-convert installed.

---

## CLI options

| Option | Description | Default |
|--------|-------------|---------|
| `--mode` | `server` or `client` | value from settings file |
| `--host` | Server IP (client mode) | `100.64.0.1` |
| `--port` | Port number | `9999` |
| `--no-tray` | Console mode without the tray (for debugging) | - |
| `--debug` | Verbose logging (DEBUG level) | - |
| `--version` | Print version and exit | - |

> The `--key` option was **removed in v2.0.0** to avoid exposing the key in the process list (`ps`, Task Manager); it's now only read from `settings.json`.

---

## Build troubleshooting

### `rsvg-convert: command not found` during build
The icon generation script needs librsvg. See the install instructions in "Generate icon assets" above.

### Linux tray icon doesn't show up (KDE Plasma Wayland)
```bash
sudo pacman -S libayatana-appindicator python-gobject
```
The venv must be created with `--system-site-packages` for GI binding access.

---

## Contributing guidelines

- When changing the protocol, update `core/protocol.py`'s `MSG_*` constants + the `main.py` handlers + the server's `broadcast` branch, all together
- Tests: `pytest tests/`
- Code style: PEP 8, type hints encouraged (existing code comments are in Korean — the maintainer's primary language)

## Requirements summary (build)

- Python 3.10+ (build-only — the release packages bundle their own Python)
- Tailscale recommended (without it, you can still connect directly by IP on the same LAN)
- Linux: `wl-clipboard` (Wayland) or `xclip`/`xsel` (X11), `libayatana-appindicator`, `python-gobject`
- Windows: no extra dependencies (pywin32 installs automatically at build time)
- macOS: no extra dependencies (pyobjc installs automatically at build time)
