; PointClicker installer (Inno Setup 6). Built by CI after `python build.py`:
;   ISCC /DAppVersion=x.y.z packaging\windows\pointclicker.iss   ->   dist\PointClicker-Setup-x.y.z.exe
; Per-user install: no administrator rights, nothing outside the user's profile.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=x.y.z
#endif
#define Repo "https://github.com/khalilullahnaul/pointclicker"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
AppId={{5F6AA550-A6B7-48F6-8835-1EFD12113CAF}
AppName=PointClicker
AppVersion={#AppVersion}
AppVerName=PointClicker {#AppVersion}
AppPublisher=Khalil Ullah Naul
AppPublisherURL={#Repo}
AppSupportURL={#Repo}/issues
AppUpdatesURL={#Repo}/releases
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\PointClicker
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\..\dist
OutputBaseFilename=PointClicker-Setup-{#AppVersion}
SetupIconFile=..\..\src\pointclicker\pointclicker.ico
UninstallDisplayIcon={app}\PointClicker.exe
UninstallDisplayName=PointClicker
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; a running PointClicker is closed (Restart Manager) so its files can be replaced
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: desktopicon; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: startup; Description: "Start PointClicker when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[InstallDelete]
; an upgrade must not mix old and new libraries
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\PointClicker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PointClicker"; Filename: "{app}\PointClicker.exe"
Name: "{autodesktop}\PointClicker"; Filename: "{app}\PointClicker.exe"; Tasks: desktopicon

[Registry]
; the same value the in-app "Launch at login" switch writes, so the two always agree
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "PointClicker"; ValueData: """{app}\PointClicker.exe"""; Tasks: startup

[Run]
Filename: "{app}\PointClicker.exe"; Description: "{cm:LaunchProgram,PointClicker}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Settings: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { "Launch at login" may have been switched on in the app after installing: always remove it }
    RegDeleteValue(HKEY_CURRENT_USER, '{#RunKey}', 'PointClicker');
    Settings := ExpandConstant('{localappdata}\PointClicker');
    { silent uninstalls keep settings: deleting user data needs an explicit "No" }
    if DirExists(Settings) and not UninstallSilent then
      if MsgBox('Keep your PointClicker settings (targets, profiles, hotkeys and logs) in case you reinstall later?',
                mbConfirmation, MB_YESNO) = IDNO then
        DelTree(Settings, True, True, True);
  end;
end;
