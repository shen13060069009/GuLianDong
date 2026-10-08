; ============================================================================
; 股联动 GuLianDong —— NSIS 安装脚本（v1.0.1 起，替代旧 stocklens.nsi）
;
; 构建（本机已装 NSIS 3.x）：
;     "C:\Program Files (x86)\NSIS\makensis.exe" -INPUTCHARSET UTF8 ^
;         /DAPP_VERSION=1.1.0 installer\guliandong.nsi
;
; 打包对象是 PyInstaller onedir 产物整个目录（dist/GuLianDong/）。
;
; 继承旧脚本的三个刻意选择：
;   * 装到 $LOCALAPPDATA\Programs\GuLianDong（用户级），不进 Program Files——
;     程序运行时要往 exe 同目录写 config.json / data/ / logs/，
;     受保护目录会「配置改了保存不了」。
;   * RequestExecutionLevel user —— 安装不提权（少一次 UAC）。
;   * Solid LZMA —— 225MB onedir 压到 ~75MB。
;
; v1.0.1 新增：完成页「开机自动启动」复选框（默认勾选）。
;   写 HKCU\...\Run 键值名 GuLianDong，卸载时删除。
; ============================================================================

!define APP_NAME  "股联动 GuLianDong"
!define APP_EXE   "GuLianDong.exe"
!define APP_ID    "GuLianDong"
!define AUTOSTART_VALUE "GuLianDong"
!define PUBLISHER "镜魔方重庆科技"

!ifndef APP_VERSION
  !define APP_VERSION "1.1.0"
!endif

; 打包源目录。默认 dist\GuLianDong，可用 /DAPP_SRC=... 覆盖——
; 场景：绿色版正在本机运行锁住 dist\GuLianDong 时，构建到备用目录再编译。
!ifndef APP_SRC
  !define APP_SRC "..\dist\GuLianDong"
!endif

; 中文界面/产品名必须：Unicode true + 编译时 -INPUTCHARSET UTF8
Unicode true

!include "MUI2.nsh"
!include "FileFunc.nsh"

Name "${APP_NAME}"
OutFile "..\dist\GuLianDong-Setup-${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\GuLianDong"
InstallDirRegKey HKCU "Software\${APP_ID}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "CompanyName"     "${PUBLISHER}"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey "LegalCopyright"  "© 2026 ${PUBLISHER}"

!define MUI_ICON   "..\build\icon.ico"
!define MUI_UNICON "..\build\icon.ico"
!define MUI_ABORTWARNING

; 完成页两个复选框：
;   RUN        = 立即启动（默认不勾，用户刚装完不必抢焦点）
;   SHOWREADME = 开机自动启动（默认勾选——这是安装包的核心卖点之一）
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 ${APP_NAME}"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_SHOWREADME ""
!define MUI_FINISHPAGE_SHOWREADME_TEXT "开机自动启动（随 Windows 登录启动）"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION EnableAutostart

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Function EnableAutostart
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
                 "${AUTOSTART_VALUE}" "$\"$INSTDIR\${APP_EXE}$\""
FunctionEnd

; ---------------------------------------------------------------- 安装
Section "主程序（必需）" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"

  ; 整个 onedir 产物（含 _internal/ 里的 Qt / onnxruntime / OCR 模型）。
  ; /x logs /x out：程序运行期自生成目录（自检日志/截图），不能发给用户。
  ; /x data 下属文件是词库要随包发；运行期新增的缓存不会出现在构建机 dist 里。
  File /r /x logs /x out "${APP_SRC}\*.*"

  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; 桌面 + 开始菜单快捷方式（v1.0.1 起固定创建，完成页复选框让位给自启动）
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\uninstall.exe"

  ; 控制面板「添加或删除程序」条目
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegStr   HKCU "Software\${APP_ID}" "InstallDir" "$INSTDIR"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayName"     "${APP_NAME}"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayIcon"     "$INSTDIR\${APP_EXE}"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "Publisher"       "${PUBLISHER}"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}" "NoRepair" 1
SectionEnd

; ---------------------------------------------------------------- 卸载
Section "Uninstall"
  ; 先结束正在运行的实例，否则文件被占用删不掉。
  ; Sleep 是必要的：taskkill 返回 ≠ 文件锁立刻释放（旧版实测踩过）。
  ExecWait 'taskkill /F /IM ${APP_EXE} /T'
  Sleep 900

  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APP_NAME}"
  RMDir /r "$INSTDIR"

  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"
  DeleteRegKey HKCU "Software\${APP_ID}"
  ; 删开机自启（勾了「开机自动启动」才存在，删不存在的值无害）
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${AUTOSTART_VALUE}"
SectionEnd
