@echo off
REM Windows 启动脚本

echo ==========================================
echo    物资设备管理系统 V6.0
echo ==========================================

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误：未找到Python，请先安装Python3.8+
    pause
    exit /b 1
)

REM 检查依赖
echo 检查依赖...
pip install -r requirements.txt >nul 2>&1

REM 检查数据库
if not exist "app.db" (
    echo 初始化数据库...
    python -c "from app import create_app, db; app = create_app(); app.app_context().push(); db.create_all(); print('数据库初始化完成')"
)

REM 启动服务
echo 启动服务...
echo 访问地址：http://127.0.0.1:5001
echo 默认账号：admin / admin123
echo.

python run.py

pause