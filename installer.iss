; installer.iss — Script Inno Setup pour MobiDesk Pro.
;
; La version est injectée par build_installer.bat via /DAppVersion, pour
; éviter un 3e endroit à synchroniser manuellement en plus de
; app/version.py et pyproject.toml.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B02A7151-ED16-4C58-BFA5-72DCB8DAE02E}
AppName=MobiDesk Pro
AppVersion={#AppVersion}
AppPublisher=Walid Mih
AppPublisherURL=https://github.com/wmih-perso/mobidesk-pro
DefaultDirName={autopf}\MobiDeskPro
DefaultGroupName=MobiDesk Pro
UninstallDisplayIcon={app}\MobiDeskPro.exe
OutputDir=dist
OutputBaseFilename=MobiDeskProSetup
SetupIconFile=app\ui\resources\app_icon.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes
WizardStyle=modern

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer une icône sur le Bureau"; GroupDescription: "Icônes supplémentaires :"; Flags: unchecked

[Dirs]
; Donne à l'utilisateur courant (non-admin) le droit d'écrire dans CE
; dossier précis après l'installation initiale — c'est ce qui permet à
; l'auto-update ultérieur (déclenché depuis l'app, sans élévation) de
; remplacer l'exe en place sans redemander UAC à chaque fois. N'affecte
; pas les permissions du reste de Program Files.
Name: "{app}"; Permissions: users-modify

[Files]
Source: "dist\MobiDeskPro.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MobiDesk Pro"; Filename: "{app}\MobiDeskPro.exe"
Name: "{group}\Désinstaller MobiDesk Pro"; Filename: "{uninstallexe}"
Name: "{autodesktop}\MobiDesk Pro"; Filename: "{app}\MobiDeskPro.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MobiDeskPro.exe"; Description: "Lancer MobiDesk Pro"; Flags: nowait postinstall skipifsilent
