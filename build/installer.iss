; Inno Setup script for Infinite Clipboard - Windows installer
; Reference docs: https://jrsoftware.org/ishelp/
;
; Build:
;   ISCC.exe /DMyAppVersion=2.1.0 /DSourceDir="%LOCALAPPDATA%\InfiniteClipboard-Build\dist\Infinite Clipboard" /DOutputDir="%LOCALAPPDATA%\InfiniteClipboard-Build\installer" build\installer.iss
;
; Conventions:
;   - ASCII-only (avoid mojibake on Windows consoles, same as build_win.bat trap #21).
;   - Per-user install ({userpf}) so admin elevation is NOT required.
;   - AppId is fixed (DO NOT change) - it's how the upgrade detection works.

#ifndef MyAppVersion
  #define MyAppVersion "2.1.0"
#endif

#ifndef SourceDir
  ; Default location written by build_win.bat (--distpath %LOCALAPPDATA%\InfiniteClipboard-Build\dist)
  #define SourceDir GetEnv("LOCALAPPDATA") + "\InfiniteClipboard-Build\dist\Infinite Clipboard"
#endif

#ifndef OutputDir
  #define OutputDir GetEnv("LOCALAPPDATA") + "\InfiniteClipboard-Build\installer"
#endif

#define MyAppName        "Infinite Clipboard"
#define MyAppPublisher   "dispather"
#define MyAppURL         "https://github.com/dispather/infinite-clipboard"
#define MyAppExeName     "Infinite Clipboard.exe"

[Setup]
; Fixed AppId - upgrade detection key. NEVER change this once a release ships.
AppId={{C5DF1725-56AE-40B7-8044-155627E7B3BB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductName={#MyAppName}

; Per-user install: %LOCALAPPDATA%\Programs\Infinite Clipboard
; No admin elevation required.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={userpf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
AllowNoIcons=yes

; Compression
Compression=lzma2
SolidCompression=yes
LZMAUseSeparateProcess=yes

; Modern wizard
WizardStyle=modern
WizardSizePercent=100

; Output
OutputDir={#OutputDir}
OutputBaseFilename=infinite-clipboard-setup-{#MyAppVersion}

; Architecture: 64-bit only (matches PyInstaller bootloader)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Uninstaller display
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}

; Misc
SetupIconFile=..\assets\generated\icon.ico
ChangesAssociations=no
ChangesEnvironment=no
CloseApplications=force
RestartApplications=no

; Always ask — otherwise Inno Setup silently auto-picks the language
; matching the Windows locale and never shows the chooser (this is why
; installs on Korean Windows always silently defaulted to Korean).
ShowLanguageDialog=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean";  MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Copy the entire PyInstaller dist tree.
; Trailing backslash + recursesubdirs picks up _internal/ and assets.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}";       Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Optional: launch app immediately after install (unchecked by default to
; let user toggle autostart from in-app settings first).
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; Leave settings.json / clipboard_history.json alone (in %APPDATA%\InfiniteClipboard).
; Uninstaller only removes program files. User data survives upgrades + reinstalls.
