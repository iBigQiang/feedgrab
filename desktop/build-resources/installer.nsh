!ifdef BUILD_UNINSTALLER
Var /GLOBAL keepInstallData
!endif

!macro customUnInit
  StrCpy $keepInstallData "0"
  ${if} ${Silent}
    StrCpy $keepInstallData "1"
  ${Else}
    MessageBox MB_ICONQUESTION|MB_YESNO|MB_DEFBUTTON2 "检测到常规卸载，默认会删除安装目录。是否保留安装目录中的 output 与 sessions 目录？" IDYES +2
    StrCpy $keepInstallData "0"
    Goto skipKeep
    StrCpy $keepInstallData "1"
    skipKeep:
  ${EndIf}
!macroend

!macro customRemoveFiles
  SetOutPath $TEMP

  ${if} $keepInstallData == "1"
    Delete "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
    Delete "$INSTDIR\${UNINSTALL_FILENAME}"
    Delete "$INSTDIR\uninstallerIcon.ico"

    Delete "$INSTDIR\chrome_100_percent.pak"
    Delete "$INSTDIR\chrome_200_percent.pak"
    Delete "$INSTDIR\d3dcompiler_47.dll"
    Delete "$INSTDIR\dxcompiler.dll"
    Delete "$INSTDIR\dxil.dll"
    Delete "$INSTDIR\ffmpeg.dll"
    Delete "$INSTDIR\icudtl.dat"
    Delete "$INSTDIR\libEGL.dll"
    Delete "$INSTDIR\libGLESv2.dll"
    Delete "$INSTDIR\LICENSE.electron.txt"
    Delete "$INSTDIR\LICENSES.chromium.html"
    Delete "$INSTDIR\resources.pak"
    Delete "$INSTDIR\snapshot_blob.bin"
    Delete "$INSTDIR\v8_context_snapshot.bin"
    Delete "$INSTDIR\version"
    Delete "$INSTDIR\vk_swiftshader_icd.json"
    Delete "$INSTDIR\vk_swiftshader.dll"
    Delete "$INSTDIR\vulkan-1.dll"

    RMDir /r "$INSTDIR\locales"
    RMDir /r "$INSTDIR\resources"
  ${else}
    RMDir /r "$INSTDIR"
  ${endif}
!macroend
