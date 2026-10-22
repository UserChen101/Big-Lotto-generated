; 页匠 PageSmith 安装包脚本（Inno Setup 6）
;
; 构建：
;   1. 先用 PyInstaller 打出 dist\PageSmith
;   2. 运行 ISCC.exe installer\PageSmith.iss
; 产物：dist\PageSmithSetup-<版本>.exe
;
; 说明
; ----
; * 显示名用中文「页匠」，安装目录与 exe 一律用 ASCII 的 PageSmith ——
;   中文路径在部分解压工具、批处理、以及某些安全软件的规则里会出问题。
; * PrivilegesRequiredOverridesAllowed：允许安装时选择"为所有用户/仅当前用户"，
;   非管理员也能安装到 %LOCALAPPDATA%\Programs\PageSmith。
; * AppId 固定不变 —— 换版本升级覆盖安装时靠它识别旧版本。
; * 文件存为 UTF-8 with BOM：Inno Setup 6 靠 BOM 判断编码，没有 BOM 时
;   中文会被按 ANSI 解读而变成乱码。

#define MyAppName "页匠"
#define MyAppNameEn "PageSmith"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "页匠"
#define MyAppExeName "PageSmith.exe"

[Setup]
AppId={{5E6E9006-B945-4A5A-B023-E9ADE7F011D8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppNameEn} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 默认「仅当前用户」安装：不弹 UAC，装到 %LOCALAPPDATA%\Programs\PageSmith；
; 需要装给全机器时可在命令行加 /ALLUSERS 走管理员模式。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=..\dist
OutputBaseFilename=PageSmithSetup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppNameEn}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; WebView2 运行时在 Win11 自带，Win10 可能缺，交给用户按提示装，不做静默部署
MinVersion=10.0
LicenseFile=license.rtf

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PageSmith\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清掉 WebView2 缓存目录（日志保留在 .pagesmith\logs，便于回溯问题）
Type: filesandordirs; Name: "{%USERPROFILE}\.pagesmith\webview"
