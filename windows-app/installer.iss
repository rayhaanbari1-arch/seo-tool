; SEO Event Tracker — Windows Installer
; Inno Setup 6

#define AppName      "SEO Event Tracker"
#define AppExeName   "SEO-Event-Tracker.exe"
#define AppPublisher "TVS Emerald"
#define AppId        "{{B3F2A1C4-9D5E-4F8B-A02C-7E61D8439250}"

; AppVersion is injected by CI: ISCC.exe /DAppVersion="2026-08-14-abc1234"
#ifndef AppVersion
#define AppVersion "1.0"
#endif

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppId={#AppId}
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
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; {#SourcePath} = directory of this .iss file (windows-app\)
; ..\  = project root where PyInstaller and the Chromium copy land
Source: "{#SourcePath}\..\dist\{#AppExeName}"; DestDir: "{app}\app"; Flags: ignoreversion
Source: "{#SourcePath}\..\playwright-browsers\*"; DestDir: "{app}\browsers"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\app\{#AppExeName}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\app\{#AppExeName}"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Run]
Filename: "{app}\app\{#AppExeName}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent
