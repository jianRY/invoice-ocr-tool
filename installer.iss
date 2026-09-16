; Inno Setup 脚本 —— 发票识别汇总工具（安装版）
; 版本号通过命令行注入：iscc /DMyAppVersion=1.0.0 installer.iss
; 输出目录通过 /O 指定；资产名保持 ASCII，中文仅用于显示名

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "发票识别汇总工具"
#define MyAppPublisher "jianRY"
#define MyAppURL "https://github.com/jianRY/invoice-ocr-tool"
#define MyAppExeName "InvoiceOcrTool.exe"

[Setup]
AppId={{9F3A17C4-6B2E-4C58-A1D7-2E5B8C41F0A3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\InvoiceOcrTool
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=InvoiceOcrTool_v{#MyAppVersion}_setup
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=no

[Messages]
SetupAppTitle=安装 - %1
SetupWindowTitle=安装 - %1
SelectDirDesc=%1 安装位置
SelectDirLabel3=安装程序将把 [name] 安装到下列文件夹。点击"下一步"继续。
SelectDirBrowseLabel=如需安装到其他位置，请点击"浏览"。
ReadyLabel1=安装程序现在准备开始安装 [name] 到你的电脑。
ReadyLabel2a=点击"安装"开始安装，或点击"上一步"检查或更改设置。
ReadyLabel2b=点击"安装"开始安装。
InstallingLabel=正在安装 [name]，请稍候…
FinishedHeadingLabel=正在完成 [name] 安装向导
FinishedLabelNoIcons=[name] 安装完成。点击"完成"退出安装向导。
FinishedLabel=[name] 安装完成。点击"完成"退出安装向导。
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。现在退出吗？
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonFinish=完成(&F)
ButtonCancel=取消
ButtonBrowse=浏览(&R)…

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务："

[Files]
Source: "dist\InvoiceOcrTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
