@echo off
rem SDP Command Prompt: the sdp command, connected to the running SDP app. The Start menu shortcut opens it.
title SDP Command Prompt
set "PATH=%~dp0;%PATH%"
if not exist "%LOCALAPPDATA%\SDP\runtime\sdp-env.cmd" (
  echo Open the SDP app first, then open this prompt again.
  exit /b 1
)
call "%LOCALAPPDATA%\SDP\runtime\sdp-env.cmd"
cd /d "%USERPROFILE%"
echo SDP Command Prompt. Signed steps need your key: set SDP_SIGNING_KEY=C:\path\to\key and SDP_SIGNER=your-name
echo Type sdp --help to see every command.
