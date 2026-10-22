; PDF Studio 安装包脚本（Inno Setup 6）
;
; 构建：
;   1. 先用 PyInstaller 打出 dist\PDFStudio
;   2. 运行 ISCC.exe installer\PDFStudio.iss
; 产物：dist\PDFStudioSetup-<版本>.exe
;
; 说明
; ----
; * PrivilegesRequiredOverridesAllowed：允许安装时选择"为所有用户/仅当前用户"，
;   非管理员也能安装到 %LOCALAPPDATA%\Programs\PDFStudio。
; * AppId 固定不变 —— 换版本升级覆盖安装时靠它识别旧版本。

#define MyAppName "PDF Studio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "PDF Studio"
#define MyAppExeName "PDFStudio.exe"

[Setup]
AppId={{A8B6A68C-9159-45FF-B6D9-7993F76C61F3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 默认「仅当前用户」安装：不弹 UAC，装到 %LOCALAPPDATA%\Programs\PDFStudio；
; 需要装给全机器时可在命令行加 /ALLUSERS 走管理员模式。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=..\dist
OutputBaseFilename=PDFStudioSetup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; WebView2 运行时在 Win11 自带，Win10 可能缺，交给用户按提示装，不做静默部署
MinVersion=10.0
LicenseFile=license.rtf

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\PDFStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清掉 WebView2 缓存目录（日志保留在 .pdfstudio\logs，便于回溯问题）
Type: filesandordirs; Name: "{%USERPROFILE}\.pdfstudio\webview"
