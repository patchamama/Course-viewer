; Course Viewer Windows Installer
; Run from repo root:
;   makensis /DEXE_PATH=dist\course-viewer.exe /DVERSION=1.0.2 nsis\installer.nsi

!ifndef EXE_PATH
  !define EXE_PATH "..\dist\course-viewer.exe"
!endif
!ifndef VERSION
  !define VERSION "1.0.0"
!endif

!define APP_NAME    "Course Viewer"
!define APP_EXE     "course-viewer.exe"
!define INSTALL_DIR "$PROGRAMFILES64\CourseViewer"
!define REG_DIR     "Directory\shell\CourseViewer"
!define REG_BG      "Directory\Background\shell\CourseViewer"
!define REG_UNINST  "Software\Microsoft\Windows\CurrentVersion\Uninstall\CourseViewer"

Name "${APP_NAME} ${VERSION}"
OutFile "course-viewer_${VERSION}_windows_setup.exe"
InstallDir "${INSTALL_DIR}"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
Unicode true

;--- Pages ---
Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

;--- Install ---
Section "Install"
  SetOutPath "$INSTDIR"
  File "${EXE_PATH}"

  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Add/Remove Programs entry
  WriteRegStr HKLM "${REG_UNINST}" "DisplayName"      "${APP_NAME}"
  WriteRegStr HKLM "${REG_UNINST}" "DisplayVersion"   "${VERSION}"
  WriteRegStr HKLM "${REG_UNINST}" "UninstallString"  '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "${REG_UNINST}" "DisplayIcon"      '"$INSTDIR\${APP_EXE}"'
  WriteRegStr HKLM "${REG_UNINST}" "Publisher"        "patchamama"

  ; Right-click on folder
  WriteRegStr HKCR "${REG_DIR}"          ""      "Open with Course Viewer"
  WriteRegStr HKCR "${REG_DIR}"          "Icon"  '"$INSTDIR\${APP_EXE}"'
  WriteRegStr HKCR "${REG_DIR}\command"  ""      '"$INSTDIR\${APP_EXE}" "%1"'

  ; Right-click on folder background (inside a folder)
  WriteRegStr HKCR "${REG_BG}"           ""      "Open with Course Viewer"
  WriteRegStr HKCR "${REG_BG}"           "Icon"  '"$INSTDIR\${APP_EXE}"'
  WriteRegStr HKCR "${REG_BG}\command"   ""      '"$INSTDIR\${APP_EXE}" "%V"'

  ; Start Menu shortcut
  CreateDirectory "$SMPROGRAMS\Course Viewer"
  CreateShortCut  "$SMPROGRAMS\Course Viewer\Course Viewer.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortCut  "$SMPROGRAMS\Course Viewer\Uninstall.lnk"     "$INSTDIR\uninstall.exe"
SectionEnd

;--- Uninstall ---
Section "Uninstall"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\uninstall.exe"
  RMDir  "$INSTDIR"

  DeleteRegKey HKCR "${REG_DIR}"
  DeleteRegKey HKCR "${REG_BG}"
  DeleteRegKey HKLM "${REG_UNINST}"

  Delete "$SMPROGRAMS\Course Viewer\Course Viewer.lnk"
  Delete "$SMPROGRAMS\Course Viewer\Uninstall.lnk"
  RMDir  "$SMPROGRAMS\Course Viewer"
SectionEnd
