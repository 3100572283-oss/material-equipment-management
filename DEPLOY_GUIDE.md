# 服务器部署验证指南

## 一、服务器信息

- **服务器地址**: 115.190.149.94
- **部署路径**: /opt/material-equipment-management
- **数据库**: material_mgmt.db (SQLite)
- **服务管理**: systemctl restart material-system

## 二、更新部署步骤

### 步骤1: 进入项目目录

```bash
cd /opt/material-equipment-management
```

### 步骤2: 拉取最新代码

```bash
git pull origin feat-phase1-material-mgmt-system-8uvyDM
```

### 步骤3: 备份数据库（重要）

```bash
cp material_mgmt.db backups/material_mgmt_$(date +%Y%m%d_%H%M%S).db
```

### 步骤4: 重启服务（自动执行数据库迁移和权限初始化）

```bash
systemctl restart material-system
sleep 5
systemctl status material-system
```

### 步骤5: 运行权限验证脚本

```bash
python3 check_permissions.py
```

期望输出：`PASS 所有检查通过！权限体系数据完整。`

### 步骤6: 验证服务可访问

```bash
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5001/auth/login
```

期望输出：`200`

### 步骤7: 登录验证权限

- 访问系统登录页
- 使用 admin / Admin@123456 登录
- 检查系统管理菜单是否正常显示
- 检查各功能模块是否正常访问

## 三、兼容性处理

### 旧角色历史权限数据

- **保留策略**: 旧数据库升级时，已有角色权限数据完全保留
- **super_admin修复**: 重启服务时，init_rbac_data()会自动为super_admin补齐所有操作权限（view/create/edit/delete/import/export/approve/print）
- **permission字段修复**: 旧数据中错误的permission值（如module_approval）会自动重新生成为标准格式（如approval:my_approvals:view）

### 升级注意事项

1. 升级前务必备份数据库
2. 重启服务时会自动执行数据库迁移（init_db_schema）
3. 重启服务时会自动执行权限数据初始化（init_rbac_data）
4. 已有用户数据、业务数据不受影响

## 四、问题排查

### 验证脚本报错误

运行 `python3 check_permissions.py` 查看具体错误项，常见原因：

- 表不存在：检查数据库迁移是否成功，查看启动日志
- 权限数量不足：检查init_rbac_data()执行日志
- admin用户未关联super_admin：检查users表role_id字段

### 服务启动失败

```bash
journalctl -u material-system -n 50
```

### 权限不生效

- 确认用户已重新登录（权限无缓存，但session需要刷新）
- 检查用户role_id是否正确关联
- 检查sys_role_menu表中对应角色的权限记录

## 五、验收标准

1. [x] 全新安装数据库，sys_menu表有完整的权限点（89个菜单+目录）
2. [x] 升级旧数据库，权限点自动补齐，不丢数据
3. [x] 超级管理员拥有所有菜单的全部8种操作权限（712个权限项）
4. [x] 新建角色配置权限，保存后回显正确
5. [x] 用对应用户登录，菜单、按钮权限都实际生效
6. [x] 修改角色权限后，用户重新登录立即生效（无缓存）
7. [x] 提供完整的验证脚本和部署步骤
