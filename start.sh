#!/bin/bash
# Linux/Mac 启动脚本

echo "=========================================="
echo "   物资设备管理系统 V6.0"
echo "=========================================="

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "错误：未找到Python3，请先安装Python3.8+"
    exit 1
fi

# 检查依赖
echo "检查依赖..."
pip3 install -r requirements.txt 2>/dev/null

# 检查数据库
if [ ! -f "app.db" ]; then
    echo "初始化数据库..."
    python3 -c "from app import create_app, db; app = create_app(); app.app_context().push(); db.create_all(); print('数据库初始化完成')"
fi

# 检查管理员用户
echo "启动服务..."
echo "访问地址：http://127.0.0.1:5001"
echo "默认账号：admin / admin123"
echo ""

# 启动Flask
python3 run.py