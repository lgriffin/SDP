@echo off
rem The sdp command, run by the bundled Python. Use it from the SDP Command Prompt while SDP is open.
"%~dp0..\python\python.exe" -c "import sys; sys.argv[0] = 'sdp'; from sdp.cli.app import main; main()" %*
