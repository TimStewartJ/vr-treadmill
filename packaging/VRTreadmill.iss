#define MyAppName "VR Treadmill"
#define MyAppExeName "VRTreadmill.exe"
#define MyAppVersion "0.1.0"

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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\native\openxr_layer\bin\vrtread_openxr_layer.dll"; DestDir: "{app}\openxr_layer\bin"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure DeleteOpenXRLayerRegistryValues(ManifestPath: String);
begin
  RegDeleteValue(HKEY_CURRENT_USER, 'Software\Khronos\OpenXR\1\ApiLayers\Explicit', ManifestPath);
  RegDeleteValue(HKEY_CURRENT_USER, 'Software\Khronos\OpenXR\1\ApiLayers\Implicit', ManifestPath);
  RegDeleteValue(HKEY_CURRENT_USER, 'Software\WOW6432Node\Khronos\OpenXR\1\ApiLayers\Explicit', ManifestPath);
  RegDeleteValue(HKEY_CURRENT_USER, 'Software\WOW6432Node\Khronos\OpenXR\1\ApiLayers\Implicit', ManifestPath);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ManifestPath: String;
begin
  if CurUninstallStep = usUninstall then begin
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'VRTreadmill');
    ManifestPath := ExpandConstant('{app}\openxr_layer\generated\XR_APILAYER_VRTREAD_treadmill.json');
    DeleteOpenXRLayerRegistryValues(ManifestPath);
  end;
end;
