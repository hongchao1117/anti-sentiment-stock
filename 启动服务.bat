@echo off
chcp 65001 >nul
cd /d "C:\Users\Administrator\Desktop\project\anti-sentiment-stock"
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak >nul
start "反散户情绪指数服务" "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" stock_server.py
echo 服务启动中，约30秒后自动打开浏览器...
