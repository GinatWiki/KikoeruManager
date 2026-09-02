; KikoeruManager Windows 安装包脚本（Inno Setup 6）
; 由 .github/workflows/release.yml 在 windows-latest 上编译：
;   ISCC.exe installer\windows\KikoeruManager.iss /DMyAppVersion=2.5.28 /DMyLangFile=<isl 绝对路径> /O<输出目录> /F<输出文件名>
; 所有相对路径均相对于脚本所在目录（installer\windows），构建产物位于
; 仓库根目录的 backend\dist（PyInstaller 产物）与 tools（内置 PostgreSQL / Redis）。
; 本文件必须保存为 UTF-8 with BOM，否则 Inno Setup 6 会按系统代码页解析中文。

#define MyAppName "KikoeruManager"
#define MyAppPublisher "GinatWiki"
#define MyAppURL "https://github.com/GinatWiki/KikoeruManager"
#define MyAppExeName "KikoeruManager.exe"
; 与 desktop_app.py 中 SINGLE_INSTANCE_MUTEX_NAME 保持一致，用于检测运行中实例
#define MyAppMutex "KikoeruManager_SingleInstance_Mutex"

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MyLangFile
  #define MyLangFile "ChineseSimplified.isl"
#endif

[Setup]
; AppId 为固定 GUID，升级安装依赖它识别同一应用，切勿变更
AppId={{64CC132F-6C78-4A68-AB42-34C3BC0E14B0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} 安装程序
; 应用把配置、内置数据库、日志写在 exe 同级的 data 目录，装到 Program Files 会因
; 缺少写入权限直接启动失败，因此默认装到当前用户目录（免管理员权限且可写）。
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
AllowUNCPath=no
UsePreviousAppDir=yes
LicenseFile=..\..\LICENSE
SetupIconFile=..\..\backend\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 免管理员安装：数据目录需要可写，全部装在当前用户目录下
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 内置 PostgreSQL / Redis 使解压后体积约 1.2GB，提前校验磁盘空间
ExtraDiskSpaceRequired=1258291200
MinVersion=10.0
AppMutex={#MyAppMutex}
CloseApplications=yes
RestartApplications=no
OutputDir=..\..\dist-installer
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-setup

[Languages]
Name: "chinesesimplified"; MessagesFile: "{#MyLangFile}"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："
Name: "runatstartup"; Description: "开机后在托盘后台自动启动"; GroupDescription: "其他任务："; Flags: unchecked

[Files]
; 主程序：托盘版（无控制台窗口）。控制台版不再打进安装包——它只在排错时用得到，
; 打进来会让安装包体积多出约 90MB，需要排错时直接下载免安装版控制台 zip 即可。
Source: "..\..\backend\dist\KikoeruManager-noconsole.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion
; 内置 PostgreSQL 运行目录（完整目录树：bin / lib / share 等）
Source: "..\..\tools\postgres\pgsql\*"; DestDir: "{app}\tools\postgres\pgsql"; Flags: ignoreversion recursesubdirs createallsubdirs
; 内置 Redis 运行目录（redis-server / redis-cli 与 msys 运行时 DLL 必须同目录）
Source: "..\..\tools\redis\*"; DestDir: "{app}\tools\redis"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; 数据目录：配置、内置数据库、日志都在里面，装好即可写入
Name: "{app}\data"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\数据目录"; Filename: "{app}\data"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 开机自启（当前用户，卸载时自动清理）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: runatstartup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "安装完成后启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
{ 安装目录位于系统受保护目录时给出提示：data 目录写不进去会导致内置数据库无法启动 }
function IsProtectedInstallDir(const Dir: string): Boolean;
var
  UpperDir: string;
  Guard: string;
begin
  Result := False;
  UpperDir := Uppercase(Dir);

  Guard := Uppercase(ExpandConstant('{pf32}'));
  if (Guard <> '') and (Pos(Guard, UpperDir) = 1) then
    Result := True;

  Guard := Uppercase(ExpandConstant('{pf64}'));
  if (Guard <> '') and (Pos(Guard, UpperDir) = 1) then
    Result := True;

  Guard := Uppercase(ExpandConstant('{win}'));
  if (Guard <> '') and (Pos(Guard, UpperDir) = 1) then
    Result := True;
end;

procedure GuardAgainstProtectedDir();
var
  Dir: string;
begin
  Dir := WizardForm.DirEdit.Text;
  if IsProtectedInstallDir(Dir) then
  begin
    MsgBox('所选安装目录位于系统受保护目录：' + #13#10 + Dir + #13#10 + #13#10 +
      '{#MyAppName} 会把配置、内置数据库和日志写在安装目录下的 data 文件夹，' +
      '装到系统目录会因缺少写入权限而无法启动。' + #13#10 + #13#10 +
      '建议改用默认位置，或其它可写目录（例如 D:\{#MyAppName}）。',
      mbInformation, MB_OK);
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    GuardAgainstProtectedDir();
  end;
end;

{ 卸载默认保留 data（内置数据库、配置、日志），避免误删已收录的库存数据 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
  Answer: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{app}\data');
    if DirExists(DataDir) then
    begin
      Answer := MsgBox('是否同时删除本地数据目录？' + #13#10 + #13#10 + DataDir + #13#10 + #13#10 +
        '其中包含内置数据库、配置文件和日志。选择“是”将永久删除已收录的库存数据且无法恢复；' +
        '选择“否”会保留数据，方便以后重装或升级。',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
      if Answer = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
