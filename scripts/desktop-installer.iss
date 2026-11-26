; 无双小爱同学 桌面版 —— Inno Setup 安装版脚本（可选）
; 用法：
;   1. 先 python scripts/build_desktop.py 生成 dist\desktop\无双小爱同学\ 目录
;   2. 安装 Inno Setup 6（https://jrsoftware.org/isdl.php），用 ISCC 编译本脚本：
;      "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" scripts\desktop-installer.iss
;   3. 产物 dist\desktop\无双小爱同学-桌面版-vX.Y.Z-Windows-安装版.exe
; 说明：装到 Program Files 时程序目录只读，数据会自动回退到 %LOCALAPPDATA%\WushuangXiaoai。
;       若不想安装，直接用便携 zip（解压双击）即可，无需本安装版。

#define MyAppName "无双小爱同学"
#define MyAppVersion "0.8.0"
#define MyAppPublisher "锤无双"
#define MyAppURL "https://www.bilibili.com/video/BV1RFyozyE8v/"
#define MyAppExeName "无双小爱同学.exe"
#define SrcDir "..\dist\desktop\无双小爱同学"

[Setup]
AppId={{7E9C4A21-5D3B-4F8E-9A6C-2B1D0E3F4A5B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist\desktop
OutputBaseFilename=无双小爱同学-桌面版-v{#MyAppVersion}-Windows-安装版
SetupIconFile=..\assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent