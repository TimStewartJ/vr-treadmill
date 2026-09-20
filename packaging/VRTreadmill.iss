#define MyAppName "VR Treadmill"
#define MyAppExeName "VRTreadmill.exe"
#define MyAppVersion "0.2.0"

[Setup]
AppId={{8A982D04-3152-48DA-BBA6-FE1D789B669B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=VR Treadmill
DefaultDirName={localappdata}\Programs\VRTreadmill
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=VRTreadmill-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}
; The app lives in the tray, so upgrades have to ask for it to be closed before the exe can be replaced.
AppMutex=VRTreadmillApp
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The OpenXR layer DLL and manifest are bundled inside the exe. The app deploys them to
; %LOCALAPPDATA%\VRTreadmill\openxr_layer\<version>-<hash>\ and registers them when OpenXR output is used.
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Unregister the OpenXR layer while the exe still exists. Leaving it registered would keep loading a DLL
; into every OpenXR game after the app is gone.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--disable-openxr-layer"; Flags: runhidden waituntilterminated; RunOnceId: "DisableOpenXrLayer"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\VRTreadmill\openxr_layer"
Type: filesandordirs; Name: "{localappdata}\VRTreadmill\logs"
Type: dirifempty; Name: "{localappdata}\VRTreadmill"

[Code]
// Belt and braces for [UninstallRun]: remove any registration that points into our deployment folder even if
// the exe could not run (deleted by hand, blocked by antivirus).
procedure RemoveOpenXRLayerRegistrations(Subkey: String);
var
  Names: TArrayOfString;
  Index: Integer;
begin
  if RegGetValueNames(HKEY_CURRENT_USER, Subkey, Names) then
    for Index := 0 to GetArrayLength(Names) - 1 do
      if Pos('\vrtreadmill\openxr_layer\', Lowercase(Names[Index])) > 0 then
        RegDeleteValue(HKEY_CURRENT_USER, Subkey, Names[Index]);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'VRTreadmill');
    RemoveOpenXRLayerRegistrations('Software\Khronos\OpenXR\1\ApiLayers\Implicit');
    RemoveOpenXRLayerRegistrations('Software\Khronos\OpenXR\1\ApiLayers\Explicit');
  end;
end;
