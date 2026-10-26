[Setup]
; Identifiant unique de GOAT pour gérer les mises à jour sans désinstaller
AppId={{8F5B891E-4A63-4C3D-889B-4D19E524FA82}
AppName=Genius Open Admin Tool
AppVersion=Pre-Alpha
AppPublisher=Genius
DefaultDirName={autopf}\GeniusOpenAdminTool
DefaultGroupName=Genius Open Admin Tool
OutputDir=Output
OutputBaseFilename=GOAT_Setup_PreAlpha
SetupIconFile=GOAT.ico
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest

; Ferme GOAT s'il tourne déjà pendant le patch/mise à jour
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Copie et met à jour l'application en ignorant les mondes, backups et configurations existantes
Source: "dist\GOAT\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "servers\*,backups\*,servers.json"

[Icons]
Name: "{group}\Genius Open Admin Tool"; Filename: "{app}\GOAT.exe"; IconFilename: "{app}\GOAT.ico"
Name: "{autodesktop}\Genius Open Admin Tool"; Filename: "{app}\GOAT.exe"; Tasks: desktopicon; IconFilename: "{app}\GOAT.ico"

[Run]
Filename: "{app}\GOAT.exe"; Description: "Lancer Genius Open Admin Tool"; Flags: nowait postinstall skipifsilent