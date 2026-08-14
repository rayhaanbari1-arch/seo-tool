; SEO Event Tracker — Windows Installer
; Inno Setup 6

#define AppName      "SEO Event Tracker"
#define AppExeName   "SEO-Event-Tracker.exe"
#define AppPublisher "TVS Emerald"
; AppId GUID — uniquely identifies this app for upgrades (never change this)
#define AppId        "{{B3F2A1C4-9D5E-4F8B-A02C-7E61D8439250}"

; AppVersion is passed on the command line during CI:
;   ISCC.exe /DAppVersion="2026-08-14-abc1234" installer.iss
#ifndef AppVersion
#define AppVersion "1.0"
#endif

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppId={#AppId}
; Install to %LOCALAPPDATA% — no admin / UAC prompt required
DefaultDirName={localappdata}\SEO-Event-Tracker
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer-dist
OutputBaseFilename=SEO-Event-Tracker-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\app\{#AppExeName}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
    Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional icons:"; \
    Flags: checked

[Files]
; Main app executable
Source: "dist\{#AppExeName}"; \
    DestDir: "{app}\app"; \
    Flags: ignoreversion

; Playwright Chromium browser
; Used for both the app window (--app mode) and screenshot capture
Source: "playwright-browsers\*"; \
    DestDir: "{app}\browsers"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";    Filename: "{app}\app\{#AppExeName}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\app\{#AppExeName}"; Tasks: desktopicon

[UninstallDelete]
; Remove everything under the install folder on uninstall
Type: filesandordirs; Name: "{app}"

[Run]
Filename: "{app}\app\{#AppExeName}"; \
    Description: "Launch {#AppName} now"; \
    Flags: nowait postinstall skipifsilent
