# 物资设备管理系统

基于 Flask + SQLAlchemy + Bootstrap 构建的工程施工物资设备全生命周期管理平台。

## 项目介绍

本系统面向建筑工程施工企业，提供从采购计划、合同管理、入库验收、库存管理、出库调拨到周转材管理的完整物资管理闭环，同时支持设备台账、巡检保养、结算管理等设备管理功能。

### 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Flask 3.0 + Flask-SQLAlchemy + Flask-Login |
| 数据库 | SQLite（开发）/ MySQL（生产） |
| ORM | SQLAlchemy 2.0 |
| 前端 | Bootstrap 5 + Jinja2 + Vanilla JS |
| 定时任务 | APScheduler |
| 导出 | openpyxl（Excel） |
| 二维码 | qrcode + Pillow |

## 环境要求

- Python 3.10+
- pip 21+
- 支持系统：macOS / Linux / Windows

## 快速启动

### 1. 克隆代码

```bash
git clone <仓库地址>
cd 物资设备管理系统
```

### 2. 创建虚拟环境

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

```bash
# 复制示例配置文件
cp .env.example .env

# 编辑 .env 文件，设置 SECRET_KEY（必填）
# 生成密钥：
python -c "import secrets; print(secrets.token_hex(32))"
```

或者手动设置环境变量：

```bash
export SECRET_KEY="your-secret-key-here"
```

### 5. 初始化数据库（首次运行自动完成）

```bash
# 方式一：直接启动（自动建表 + 初始化基础数据）
python run.py

# 方式二：手动初始化（可选）
python init_db.py
```

### 6. 访问系统

- 访问地址：http://127.0.0.1:5001
- 默认管理员账号：`admin`
- 默认密码：`Admin@2024`
- 其他默认账号：`editor` / `viewer`（密码同上）

> 生产环境部署后，请务必修改默认管理员密码。

## 功能模块列表

### 工作台
- 待办事项、通知公告、快捷入口
- 库存查询、合同预警、本月入库统计

### 主数据管理
- 物资分类管理（多级分类）
- 供应商管理（资质有效期预警）
- 物资编码管理（支持条码/二维码生成）
- 项目档案管理

### 采购合同
- 采购合同台账
- 合同明细管理
- 合同结算与付款管理
- 供应商对账

### 库存管理
- 入库管理（采购入库、调拨入库等）
- 出库管理（领用出库、调拨出库等）
- 库存查询与预警
- 库存盘点
- 物资调拨
- 物资报废
- 库存流水查询

### 周转材管理
- 周转材台账
- 周转材领用/归还
- 周转材库存
- 租金结算

### 设备管理
- 设备台账
- 设备巡检
- 设备保养
- 设备租赁结算
- 设备报废

### 统计报表
- 入库统计
- 出库统计
- 库存报表
- 混凝土小票统计
- 钢材重量自动换算

### 系统管理
- 用户管理（含密码重置、强制修改密码）
- 角色与权限（RBAC）
- 菜单管理
- 组织架构（部门/项目部）
- 数据字典
- 审批流程配置
- 系统配置
- 操作日志 / 登录日志
- 数据备份与恢复

## 部署方式

### 本地开发启动

```bash
# 默认端口 5001，debug 模式
python run.py
```

### 生产部署建议

1. **更换数据库**：将 SQLite 替换为 MySQL/PostgreSQL
   ```bash
   # .env 中设置
   DATABASE_URL=mysql+pymysql://user:password@localhost/material_mgmt
   ```

2. **设置强密钥**：
   ```bash
   export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
   ```

3. **使用生产 WSGI 服务器**：
   ```bash
   pip install gunicorn
   gunicorn -w 4 -b 0.0.0.0:5001 "app:create_app()"
   ```

4. **配置反向代理**（Nginx/Apache）

5. **设置文件上传目录权限**：
   ```bash
   mkdir -p /var/www/uploads
   chmod 755 /var/www/uploads
   ```

## 目录结构

```
.
├── app/                    # 应用主目录
│   ├── __init__.py         # Flask 应用工厂
│   ├── models.py           # 数据模型
│   ├── auth/               # 认证模块（登录/密码管理）
│   ├── admin/              # 系统管理（用户/角色/菜单）
│   ├── system/             # RBAC + 组织架构
│   ├── dict_mgr/           # 数据字典
│   ├── contracts/          # 合同管理
│   ├── stock/              # 出入库管理
│   ├── inventory/          # 库存管理
│   ├── equipment/          # 设备管理
│   ├── concrete/           # 混凝土管理
│   ├── reports/            # 统计报表
│   ├── api/                # API 接口
│   ├── ai/                 # AI 助手
│   ├── static/             # 静态资源
│   │   ├── uploads/        # 上传文件
│   │   └── qr_codes/       # 生成的二维码
│   └── templates/          # Jinja2 模板
├── config.py               # 应用配置（敏感信息从环境变量读取）
├── config.example.py       # 配置示例文件
├── .env.example            # 环境变量示例
├── requirements.txt        # Python 依赖
├── run.py                  # 启动入口
├── init_db.py              # 数据库初始化脚本
├── test_data.sql           # 演示测试数据（可选导入）
└── README.md               # 本文件
```

## 测试数据导入（可选）

如需导入演示数据以体验完整功能：

```bash
# 先确保数据库已初始化并运行过至少一次
sqlite3 material_mgmt.db < test_data.sql
```

> 注意：test_data.sql 包含大量业务测试数据，仅用于演示，不建议在生产环境使用。

## 更新日志

- 角色管理排序号自动化（新增自动+1，删除自动重排）
- 管理员重置用户密码（支持默认密码填充、强制下次登录修改）
- 数据回收站与物理删除机制
- 审批流程可视化设计器
- 周转材全生命周期管理
- 设备台账与巡检保养

## License

MIT License
