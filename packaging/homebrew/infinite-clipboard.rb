cask "infinite-clipboard" do
  # ⚠️ DRAFT — 실제 배포 전 반드시 확인/수정할 것 (packaging/README.md 참조):
  #   1. sha256 두 값은 플레이스홀더다. 이 cask 를 쓸 릴리스(v3.0.9+, DMG 파일명에
  #      -apple-silicon/-intel suffix 가 들어간 첫 릴리스)가 나온 뒤
  #      `shasum -a 256 <dmg>` 로 실제 값을 채운다.
  #   2. `depends_on macos:` 최소 버전은 추정치 — 실제 CustomTkinter/Tk 8.6 빌드가
  #      지원하는 최저 macOS 버전으로 조정 필요(미검증).
  #   3. Homebrew 자체 cask 스타일 검사(`brew audit --cask`, `brew style --cask`)를
  #      실행해 통과 확인 후 제출.
  version "3.0.8"

  on_arm do
    sha256 "REPLACE_WITH_ARM64_DMG_SHA256"
    url "https://github.com/dispather/infinite-clipboard/releases/download/v#{version}/Infinite.Clipboard.#{version}-apple-silicon.dmg"
  end

  on_intel do
    sha256 "REPLACE_WITH_INTEL_DMG_SHA256"
    url "https://github.com/dispather/infinite-clipboard/releases/download/v#{version}/Infinite.Clipboard.#{version}-intel.dmg"
  end

  name "Infinite Clipboard"
  desc "Real-time clipboard and file sharing tray app between your PCs over your own Tailscale network"
  homepage "https://github.com/dispather/infinite-clipboard"

  # 자동 서명/공증 없음(packaging/README.md 및 CLAUDE.md 함정 참조) — 앱 자체
  # 업데이터도 없어 새 버전은 `brew upgrade --cask` 로만 받는다.
  auto_updates false
  depends_on macos: ">= :big_sur"

  app "Infinite Clipboard.app"

  # DMG 가 애드혹 서명(codesign --sign -)만 돼있어 Gatekeeper 가 quarantine
  # 플래그를 붙인다 — README 의 수동 `xattr -dr com.apple.quarantine` 안내와
  # 동일한 조치를 설치 직후 자동 수행(brew cask 표준 패턴).
  postflight do
    system_command "/usr/bin/xattr",
                    args: ["-dr", "com.apple.quarantine", "#{appdir}/Infinite Clipboard.app"],
                    sudo: false
  end

  zap trash: [
    "~/Library/Application Support/InfiniteClipboard",
    "~/Library/LaunchAgents/com.dispather.infinite-clipboard.plist",
  ]
end
