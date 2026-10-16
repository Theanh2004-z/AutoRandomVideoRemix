; Keep this AppId stable across every version.
#ifndef AppVersion
  #error AppVersion must be supplied by scripts/build_installer.py
#endif
#ifndef DistDir
  #error DistDir must be supplied by scripts/build_installer.py
#endif
#ifndef BuildOutputDir
  #error BuildOutputDir must be supplied by scripts/build_installer.py
#endif
#ifndef AppUserModelID
  #define AppUserModelID "Theanh2004z.AutoRandomVideoRemix"
#endif
#ifndef SetupAppId
  #define SetupAppId "{{2966602D-716B-4679-A59D-74BFE8054210}"
#endif
#ifndef StartMenuRoot
  #define StartMenuRoot "{autoprograms}"
#endif
#ifndef DesktopRoot
  #define DesktopRoot "{autodesktop}"
#endif
#ifndef RegistryExeName
  #define RegistryExeName "AutoRandomVideoRemix.exe"
#endif

[Setup]
AppId={#SetupAppId}
AppName=REMIX - AutoRandomVideoRemix
AppVersion={#AppVersion}
AppPublisher=Theanh2004-z
AppPublisherURL=https://github.com/Theanh2004-z/AutoRandomVideoRemix
AppSupportURL=https://github.com/Theanh2004-z/AutoRandomVideoRemix/issues
DefaultDirName={localappdata}\Programs\AutoRandomVideoRemix
DefaultGroupName=REMIX
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
DisableProgramGroupPage=yes
WizardStyle=modern
SetupIconFile=..\assets\remix.ico
UninstallDisplayIcon={app}\App\AutoRandomVideoRemix.exe
; App/ is replaced by the updater. Uninstall/ must remain outside it.
UninstallFilesDir={app}\Uninstall
AppMutex={#AppUserModelID}
CloseApplications=yes
RestartApplications=no
Compression=lzma2/fast
SolidCompression=yes
OutputDir={#BuildOutputDir}
OutputBaseFilename=AutoRandomVideoRemix-{#AppVersion}-windows-x64-Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng REMIX trên Desktop"; GroupDescription: "Biểu tượng ứng dụng:"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}\App"; Excludes: "user_settings.json,history\*,cache\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{#StartMenuRoot}\REMIX (AutoRandomVideoRemix)"; Filename: "{app}\App\AutoRandomVideoRemix.exe"; WorkingDir: "{app}\App"; IconFilename: "{app}\App\AutoRandomVideoRemix.exe"; AppUserModelID: "{#AppUserModelID}"
Name: "{#DesktopRoot}\REMIX"; Filename: "{app}\App\AutoRandomVideoRemix.exe"; WorkingDir: "{app}\App"; IconFilename: "{app}\App\AutoRandomVideoRemix.exe"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#RegistryExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\App\AutoRandomVideoRemix.exe"; Flags: uninsdeletekey

[Run]
Filename: "{app}\App\AutoRandomVideoRemix.exe"; Description: "Mở REMIX"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only managed program files; settings, history and exported media live elsewhere.
Type: filesandordirs; Name: "{app}\App"

[Code]
function OwnedBackupName(const Name: String): Boolean;
var
  I: Integer;
  Suffix: String;
begin
  Result := False;
  if Copy(Name, 1, 12) <> '.App.backup_' then Exit;
  Suffix := Copy(Name, 13, MaxInt);
  if Length(Suffix) <> 32 then Exit;
  for I := 1 to Length(Suffix) do
    if Pos(Suffix[I], '0123456789abcdef') = 0 then Exit;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Item: TFindRec;
  Backup: String;
begin
  if CurUninstallStep <> usPostUninstall then Exit;
  if FindFirst(ExpandConstant('{app}\.App.backup_*'), Item) then
  begin
    try
      repeat
        Backup := ExpandConstant('{app}\') + Item.Name;
        if OwnedBackupName(Item.Name) and ((Item.Attributes and $400) = 0) and
           FileExists(Backup + '\.remix-update-backup.json') then
          DelTree(Backup, True, True, True);
      until not FindNext(Item);
    finally
      FindClose(Item);
    end;
  end;
end;
