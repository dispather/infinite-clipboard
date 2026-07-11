# Packaging (package manager submissions)

Draft/verified packaging manifests for third-party package managers, prepared
2026-07-11 to reduce install friction (the repo went public 2026-07-10 with
zero external distribution beyond GitHub Releases). None of these are
auto-submitted — each requires the maintainer's own account on the target
ecosystem, so this directory holds the artifacts for a human to review and
push.

## AUR — `aur/infinite-clipboard-bin/` (verified, ready to submit)

Local-build-only `build/PKGBUILD` (used by `build_linux.sh`/CI) can't be
submitted to AUR as-is — it assumes a pre-built `dist/` on disk and has no
`source=()`. This is a separate `-bin` package that fetches the actual
`v3.0.8` GitHub Release asset and re-packages it. **Verified 2026-07-11** on
this machine (CachyOS/Arch) with a real `makepkg -f`: source download,
sha256 checksum validation (confirmed it actually rejects a tampered hash,
not just a no-op), `package()`, and the resulting `.pkg.tar.zst` contents
(`opt/`, `usr/bin`, `.desktop`, icon, license) all check out. Not installed
system-wide during verification — this machine runs the real
infinite-clipboard service in production and installing over it would be
disruptive.

To submit:
1. Create an AUR account at <https://aur.archlinux.org> if you don't have
   one, and add an SSH public key under your account settings.
2. `git clone ssh://aur@aur.archlinux.org/infinite-clipboard-bin.git`
3. Copy `PKGBUILD` and `.SRCINFO` from this directory into that clone.
4. `git add -A && git commit -m "Initial import: 3.0.8" && git push`

**Ongoing maintenance**: every new release needs `pkgver` and `sha256sums`
bumped to match (`sha256sum <downloaded .pkg.tar.zst>`), then
`makepkg --printsrcinfo > .SRCINFO` regenerated before pushing. This isn't
automated yet — `build_linux.sh` only auto-bumps `build/PKGBUILD` (the local
build one), not this AUR one. Worth wiring into the release process later if
this becomes a real maintenance burden.

## Homebrew Cask — `homebrew/infinite-clipboard.rb` (draft, unverified)

Targets a **personal tap** (`dispather/homebrew-tap`), not the official
`homebrew/homebrew-cask` repo — much lower friction (no upstream review,
fully under your control) and the standard path before a project has enough
traction for the official tap's popularity bar.

**Before using this file**, it has two placeholder `sha256` values
(`REPLACE_WITH_ARM64_DMG_SHA256` / `REPLACE_WITH_INTEL_DMG_SHA256`) because
the DMG filenames it references (`Infinite.Clipboard.X.Y.Z-apple-silicon.dmg`
/ `-intel.dmg`) don't exist yet — they're the *new* naming scheme from this
session's macOS Intel CI work (`.github/workflows/build.yml`), which only
takes effect starting with the **next** tagged release after this one ships.
Once that release is out: `shasum -a 256` each DMG, paste the values in,
double check `depends_on macos:` against what CustomTkinter/Tk 8.6 actually
requires (currently an unverified guess), and run
`brew audit --cask --strict infinite-clipboard.rb` /
`brew style --cask infinite-clipboard.rb` before publishing.

To publish to your own tap:
1. `gh repo create dispather/homebrew-tap --public`
2. Clone it, create `Casks/infinite-clipboard.rb` with this file's content
   (filled in), commit, push.
3. Users install with `brew tap dispather/tap && brew install --cask infinite-clipboard`.

## winget — `winget/manifests/...` (draft, unverified)

Three-file manifest set (`version` / `installer` / `defaultLocale`) at the
path winget-pkgs expects (`manifests/d/dispather/InfiniteClipboard/3.0.8/`).
Silent-install switches assume Inno Setup's default `/VERYSILENT /NORESTART`
(installer.iss doesn't override these, so this should be correct) but
**`ProductCode` is an unverified guess** — Inno Setup's uninstall registry
key is conventionally `{AppId}_is1` where AppId is the fixed
`{C5DF1725-56AE-40B7-8044-155627E7B3BB}` from `installer.iss`, but this must
be confirmed on a real Windows install (`HKCU\...\Uninstall\` key name)
before submitting, or winget's upgrade/uninstall detection could misbehave.

To submit:
1. Verify `ProductCode` on real Windows (see above).
2. Ideally regenerate via Microsoft's `wingetcreate` tool instead of hand
   validation — it validates schema and can test-install:
   `wingetcreate new https://github.com/dispather/infinite-clipboard/releases/download/v3.0.8/infinite-clipboard-setup-3.0.8.exe`
3. Fork `microsoft/winget-pkgs`, add the manifest folder, open a PR (any
   GitHub account can do this — no special winget account needed).

## Not attempted this session

- **Official `homebrew/homebrew-cask`**: needs enough external notability to
  clear their PR bar — the personal tap above is the right first step.
- **Apple code signing/notarization**: separately tracked — needs a paid
  Apple Developer account, which is the user's action, not something that
  can be prepared in advance the way these package manifests could.
