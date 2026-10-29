; The SDP Command Prompt: the sdp command, connected to the running app, for the signed steps the UI
; shows as commands to copy (freeze, approve, promote).
!macro customInstall
  CreateShortCut "$SMPROGRAMS\SDP Command Prompt.lnk" "$SYSDIR\cmd.exe" '/k "$INSTDIR\resources\sdp\bin\sdp-shell.cmd"' "$INSTDIR\${APP_EXECUTABLE_FILENAME}" 0
!macroend

!macro customUnInstall
  Delete "$SMPROGRAMS\SDP Command Prompt.lnk"
!macroend
