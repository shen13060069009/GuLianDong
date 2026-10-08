; ============================================================================
; 股镜 StockLens —— NSIS 安装脚本
;
; 构建（在 build/tools/nsis-3.10 解压目录下）：
;     makensis.exe /DAPP_VERSION=1.0.0 ..\..\installer\stocklens.nsi
;
; 打包对象是 PyInstaller 的 onedir 产物整个目录（dist/StockLens/）。
;
; 两个刻意选择：
;   * 装到 $LOCALAPPDATA\Programs\StockLens（用户级），而不是 Program Files。
;     程序运行时要往 exe 同目录写 config.json / data/ / logs/，
;     装到受保护目录会导致「配置改了保存不了」。
;   * RequestExecutionLevel user —— 安装过程不需要提权（少一次 UAC）。
;     程序自己带 admin manifest，启动时才会弹。
; ============================================================================

!define APP_NAME  "股镜 StockLens"
!define APP_EXE   "StockLens.exe"
!define APP_ID    "StockLens"
!define PUBLISHER "镜魔方重庆科技"

!ifndef APP_VERSION
  !define APP_VERSION "1.0.0"
!endif

; 打包源目录。默认 dist\StockLens，可用 /DAPP_SRC=... 覆盖 ——
; 场景：绿色版正在本机运行时会锁住 dist\StockLens，此时构建到备用目录再编译安装包。
!ifndef APP_SRC
  !define APP_SRC "..\dist\StockLens"
!endif

; 脚本里有中文（注释、APP_NAME、MUI 中文界面），必须显式声明：
;   Unicode true        —— 生成 Unicode 安装程序，界面中文才不会乱码
;   编译时加 -INPUTCHARSET UTF8 —— makensis 默认按系统 ANSI 读脚本，
;                                  遇到 UTF-8 中文会直接 "Bad text encoding" 中止
Unicode true

!include "MUI2.nsh"
!include "FileFunc.nsh"

Name "${APP_NAME}"
OutFile "..\dist\StockLens-Setup-${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\StockLens"
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
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 ${APP_NAME}"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_SHOWREADME ""
!define MUI_FINISHPAGE_SHOWREADME_TEXT "创建桌面快捷方式"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION CreateDesktopShortcut

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

Function CreateDesktopShortcut
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
FunctionEnd

; ---------------------------------------------------------------- 安装
Section "主程序（必需）" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"

  ; 整个 onedir 产物（含 _internal/ 里的 Qt / onnxruntime / OCR 模型）
  ;
  ; /x logs /x out 是必须的：这两个是程序运行期自己生成的目录。
  ; 真机验证（tools/verify_build.py）会在 dist/StockLens/ 下留下
  ; logs/stocklens.log 和 out/gui_overlay_test*.png，
  ; 不排除的话会跟着安装包分发给用户 —— 把测试日志和截图发出去很难看。
  File /r /x logs /x out "${APP_SRC}\*.*"

  ; 首次安装就给一份可编辑的 config.json 与 data/（程序内也有自举逻辑，
  ; 这里先铺好，用户装完立刻能看到、能改）
  WriteUninstaller "$INSTDIR\uninstall.exe"

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
  ; Sleep 是必要的：taskkill 返回 ≠ 文件锁立刻释放，实测不加会残留 StockLens.exe。
  ExecWait 'taskkill /F /IM ${APP_EXE} /T'
  Sleep 900

  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  ; ⚠ 不要逐个文件 Delete 完再 RMDir —— 实测出现过「卸载器那一下删不掉，
  ;   但事后手工删完全没问题」的情况（开始菜单被资源管理器短暂占用）。
  ;   逐个 Delete 只删掉它删得动的那个，反而留下一半；RMDir /r 递归收口更稳。
  RMDir /r "$SMPROGRAMS\${APP_NAME}"

  ; ⚠ 主程序本体也要删。漏掉它会留下一个打不开的 exe 在安装目录里，
  ;   用户会以为「没卸载掉」。（第一版就漏了，卸载测试才发现）
  ; 用 RMDir /r 一次性收口，别枚举 —— 枚举写法只要漏一项就前功尽弃。
  RMDir /r "$INSTDIR"

  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"
  DeleteRegKey HKCU "Software\${APP_ID}"
SectionEnd
