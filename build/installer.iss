; ============================================================================
;  FormatMaster 安装包配置（Inno Setup 6）
;  编译：ISCC.exe build\installer.iss
;  产物：dist_installer\FormatMaster_Setup_v1.1.0.exe
; ============================================================================

#define MyAppName "FormatMaster"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "ErBai"
#define MyAppExeName "FormatMaster.exe"
#define MyAppDesc "万能格式转换器"

[Setup]
AppId={{8F3A2C71-4B9E-4D62-A3E5-1C7D9B4E5A20}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion} {#MyAppDesc}
AppPublisher={#MyAppPublisher}
AppComments=视频 / 音频 / 图像 / 文档 / 压缩包 / 平台加密音乐 全格式互转

DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

OutputDir=..\dist_installer
OutputBaseFilename=FormatMaster_Setup_v{#MyAppVersion}
SetupIconFile=..\app\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppDesc}

; LZMA 极限压缩——程序带两个 79MB 的 FFmpeg 二进制，必须压到极致
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes

WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "chinese"; MessagesFile: "tools\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "..\dist\FormatMaster\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Comment: "{#MyAppDesc}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Comment: "{#MyAppDesc}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 清掉运行期可能产生的缓存
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
