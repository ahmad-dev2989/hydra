; Inno Setup Script for HYDRA System Interface
#define AppName "HYDRA"
#define AppFullName "HYDRA System Interface"
#define AppVersion "2.0.0"
#define AppPublisher "HYDRA Intelligence"
#define AppExeName "Hydra.exe"
#define SourceDir "dist\Hydra"
#define OutputDir "dist\installer"
#define IconFile "assets\hydra_icon.ico"

[Setup]
AppId={{E82F4892-A29A-4BE7-9BF9-E938A4B6E1F0}
AppName={#AppFullName}
AppVersion={#AppVersion}
AppVerName={#AppFullName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=setup
SetupIconFile={#IconFile}
UninstallDisplayName={#AppFullName}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/normal
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppFullName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\hydra_icon.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\hydra_icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppFullName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
