@echo off
rem SDP Command Prompt: the sdp command, connected to the running SDP app. The Start menu shortcut opens it.
title SDP Command Prompt
set "PATH=%~dp0;%PATH%"
rem The app keeps its data in SDP_DESKTOP_DATA when that is set, else in %LOCALAPPDATA%\SDP.
if defined SDP_DESKTOP_DATA (set "SDP_DATA=%SDP_DESKTOP_DATA%") else (set "SDP_DATA=%LOCALAPPDATA%\SDP")
if not exist "%SDP_DATA%\runtime\sdp-env.cmd" (
  echo Open the SDP app first, then open this prompt again.
  exit /b 1
)
call "%SDP_DATA%\runtime\sdp-env.cmd"
cd /d "%USERPROFILE%"
echo SDP Command Prompt. Signed steps need your key: set SDP_SIGNING_KEY=C:\path\to\key and SDP_SIGNER=your-name
echo Type sdp --help to see every command.
