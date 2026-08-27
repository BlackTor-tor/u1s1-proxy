@echo off
rem u1s1 反向代理启动脚本（Windows）
cd /d "%~dp0"
python u1s1_proxy.py %*
pause
