# 第二阶段部署验证指南：项目可见范围控制

## 一、部署前检查

### 1. 确认代码已提交
```bash
cd /Users/zhengfeilong/.trae-cn/worktrees/物资设备管理/feat-phase1-material-mgmt-system-8uvyDM
git status
git add -A
git commit -m "feat: 第二阶段 - 项目可见范围控制完整实现"
git push origin feat-phase1-material-mgmt-system-8uvyDM
```

### 2. 服务器更新代码
```bash
cd /opt/material-equipment-management
git pull origin feat-phase1-material-mgmt-system-8uvyDM
```

### 3. 数据库备份
```bash
cp material_mgmt.db backups/material_mgmt_$(date +%Y%m%d_%H%M%S).db
```

### 4. 重启服务
```bash
systemctl restart material-system
sleep 5
systemctl status material-system
```

## 二、验证测试脚本

### 1. 运行项目可见性测试
```bash
cd /opt/material-equipment-management
python3 test_phase2_project_visibility.py
```

### 2. 预期输出
- **admin用户**：
  - 数据权限: all
  - get_allowed_projects() 返回 None
  - 可见项目: 全部项目
  - 可查看全部项目选项: ✓

- **test_user_a1用户**（本部门权限）：
  - 数据权限: dept
  - 可见项目: 仅本部门关联的项目（1个）
  - 可查看全部项目选项: ✗

- **test_user_b1用户**（本部门权限）：
  - 数据权限: dept
  - 可见项目: 仅本部门关联的项目（1个）
  - 可查看全部项目选项: ✗

## 三、Web界面验证

### 1. admin账号登录测试
**账号**: admin  
**密码**: Admin@123456

**验证步骤**：
1. 登录系统
2. 查看顶部项目切换器
3. 点击切换器，验证"全部项目"选项存在
4. 验证能看到所有在建项目（active状态）
5. 切换不同项目，验证业务数据正确过滤

**预期结果**：
- ✓ 项目切换器显示"全部项目"选项
- ✓ 能看到所有在建项目（至少5个以上）
- ✓ 切换项目后列表数据正确刷新

### 2. 本部门权限账号测试
**账号**: test_user_a1  
**密码**: Test@123456

**验证步骤**：
1. 登录系统
2. 查看顶部项目切换器
3. 验证只显示自己部门关联的项目名称
4. 验证没有"全部项目"选项
5. 尝试手动访问其他项目URL（应被拒绝）

**预期结果**：
- ✓ 项目切换器只显示1个项目（自己部门的）
- ✓ 没有"全部项目"选项
- ✓ 切换器不可点击（只有一个项目时自动锁定）
- ✓ 访问其他项目ID返回权限错误

### 3. 项目访问权限测试
**账号**: test_user_b1  
**密码**: Test@123456

**验证步骤**：
1. 登录系统
2. 尝试直接访问其他项目的入库列表URL：
   ```
   http://115.190.149.94/stock_in?project_id=2
   ```
3. 验证是否被拒绝访问

**预期结果**：
- ✓ 返回403错误或重定向到自己的项目
- ✓ 无法查看其他项目的业务数据

## 四、数据库验证

### 1. 检查用户数据权限配置
```sql
-- 查看测试账号的数据权限
SELECT u.id, u.username, u.dept_id, d.dept_name, d.dept_type, 
       r.role_code, r.role_name, r.data_scope
FROM users u
LEFT JOIN sys_dept d ON u.dept_id = d.id
LEFT JOIN sys_role r ON u.role_id = r.id
WHERE u.username IN ('admin', 'test_user_a1', 'test_user_b1');
```

**预期结果**：
- admin: data_scope='all', dept_type=NULL
- test_user_a1: data_scope='dept', dept_type='project'
- test_user_b1: data_scope='dept', dept_type='project'

### 2. 检查项目-部门关联
```sql
-- 查看项目部类型部门关联的项目
SELECT d.id, d.dept_name, d.dept_type, d.project_id, p.name as project_name
FROM sys_dept d
LEFT JOIN projects p ON d.project_id = p.id
WHERE d.dept_type = 'project'
ORDER BY d.id;
```

**预期结果**：
- 每个项目部类型部门都关联一个项目
- 部门名称与项目名称一致

### 3. 检查用户-项目分配
```sql
-- 查看用户被直接分配的项目
SELECT u.username, p.name as project_name, up.is_main
FROM sys_user_project up
JOIN users u ON up.user_id = u.id
JOIN projects p ON up.project_id = p.id
ORDER BY u.username, up.is_main DESC;
```

## 五、业务查询验证

### 1. 入库列表项目过滤
**账号**: test_user_a1

**验证步骤**：
1. 登录系统
2. 进入"库存管理 > 入库管理"
3. 查看入库列表
4. 验证只显示自己项目的入库单

**预期结果**：
- ✓ 列表只包含当前项目的入库单
- ✓ 无法看到其他项目的入库单

### 2. 合同台账项目过滤
**账号**: test_user_b1

**验证步骤**：
1. 登录系统
2. 进入"采购合同 > 合同台账"
3. 查看合同列表
4. 验证只显示自己项目的合同

**预期结果**：
- ✓ 列表只包含当前项目的合同
- ✓ 无法看到其他项目的合同

## 六、验收标准

### 必须通过的测试用例：
1. ✅ admin账号能看到所有项目并有"全部项目"选项
2. ✅ 本部门权限用户只能看到自己部门关联的项目
3. ✅ 项目切换器正确显示可见项目列表
4. ✅ 业务列表正确过滤当前项目的数据
5. ✅ 无权限项目URL访问被拒绝（403或重定向）
6. ✅ 测试脚本输出符合预期

### 验证完成标志：
- 所有测试用例通过
- Web界面操作符合预期
- 数据库查询结果正确
- 测试脚本输出无错误

## 七、问题排查

### 如果项目切换器显示异常：
1. 检查用户数据权限配置：
   ```python
   user.get_data_scope()
   user.get_allowed_projects()
   ```
2. 检查部门-项目关联：
   ```python
   dept.dept_type
   dept.project_id
   ```
3. 查看后端日志：
   ```bash
   journalctl -u material-system -f
   ```

### 如果业务数据过滤失效：
1. 检查session中的current_project_id：
   ```python
   session.get('current_project_id')
   ```
2. 检查apply_data_scope函数调用：
   ```python
   from app.utils import apply_data_scope
   query = apply_data_scope(query, Model)
   ```
3. 查看SQL查询日志：
   ```bash
   # 启用SQL日志
   app.config['SQLALCHEMY_ECHO'] = True
   ```

## 八、回滚方案

如果验证失败需要回滚：
```bash
cd /opt/material-equipment-management
git reset --hard HEAD~1
systemctl restart material-system
```

## 九、完成标志

- [ ] 代码已推送到GitHub
- [ ] 服务器代码已更新
- [ ] 数据库已备份
- [ ] 服务重启成功
- [ ] 测试脚本运行通过
- [ ] Web界面验证通过
- [ ] 数据库验证通过
- [ ] 业务查询验证通过