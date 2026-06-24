; Course Viewer Windows Installer
; Usage: makensis installer.nsi
; Requires: course-viewer.exe to be present in ../dist/

!define APP_NAME "Course Viewer"
!define APP_EXE "course-viewer.exe"
!define INSTALL_DIR "$PROGRAMFILES64\CourseViewer"
!define REG_SHELL "Directory\shell\CourseViewer"

Name "${APP_NAME}"
OutFile "course-viewer-setup.exe"
InstallDir "${INSTALL_DIR}"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

;--- Pages ---
Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

;--- Install ---
Section "Install"
  SetOutPath "$INSTDIR"
  File "..\dist\${APP_EXE}"

  ; Write uninstaller
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Add to Add/Remove Programs
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CourseViewer" \
    "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CourseViewer" \
    "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CourseViewer" \
    "DisplayIcon" '"$INSTDIR\${APP_EXE}"'

  ; Right-click context menu on folders
  WriteRegStr HKCR "${REG_SHELL}" "" "Open with Course Viewer"
  WriteRegStr HKCR "${REG_SHELL}" "Icon" '"$INSTDIR\${APP_EXE}"'
  WriteRegStr HKCR "${REG_SHELL}\command" "" '"$INSTDIR\${APP_EXE}" "%1"'

  ; Also add to Desktop\shell (for Desktop folder right-click)
  WriteRegStr HKCR "Directory\Background\shell\CourseViewer" "" "Open with Course Viewer"
  WriteRegStr HKCR "Directory\Background\shell\CourseViewer" "Icon" '"$INSTDIR\${APP_EXE}"'
  WriteRegStr HKCR "Directory\Background\shell\CourseViewer\command" "" '"$INSTDIR\${APP_EXE}" "%V"'

  ; Create Start Menu shortcut
  CreateDirectory "$SMPROGRAMS\Course Viewer"
  CreateShortCut "$SMPROGRAMS\Course Viewer\Course Viewer.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortCut "$SMPROGRAMS\Course Viewer\Uninstall.lnk" "$INSTDIR\uninstall.exe"
SectionEnd

;--- Uninstall ---
Section "Uninstall"
  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"

  ; Remove registry keys
  DeleteRegKey HKCR "${REG_SHELL}"
  DeleteRegKey HKCR "Directory\Background\shell\CourseViewer"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CourseViewer"

  ; Remove Start Menu
  Delete "$SMPROGRAMS\Course Viewer\Course Viewer.lnk"
  Delete "$SMPROGRAMS\Course Viewer\Uninstall.lnk"
  RMDir "$SMPROGRAMS\Course Viewer"
SectionEnd
