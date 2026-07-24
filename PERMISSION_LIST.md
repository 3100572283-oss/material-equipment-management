# 物资设备管理系统 - 权限点清单

## 权限标识命名规范
`模块:功能:操作`

例如：`stock:in:view`、`contract:list:export`

## 操作类型说明
| 操作 | 标识 | 说明 |
|------|------|------|
| 查看 | view | 查看列表/详情 |
| 新增 | create | 创建新记录 |
| 编辑 | edit | 修改已有记录 |
| 删除 | delete | 删除记录 |
| 导入 | import | 从Excel导入 |
| 导出 | export | 导出到Excel |
| 审批 | approve | 审批单据 |
| 打印 | print | 打印单据 |

## 权限点完整清单

### 一、工作台 (workbench)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 首页 | `workbench:index:view` | 查看首页仪表盘 |
| 我的审批 | `workbench:approval:view` | 查看我的审批列表 |
| 我的审批 | `workbench:approval:approve` | 审批操作 |

### 二、基础数据 (basic_data)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 项目管理 | `basic:project:view` | 查看项目列表 |
| 项目管理 | `basic:project:create` | 新增项目 |
| 项目管理 | `basic:project:edit` | 编辑项目 |
| 项目管理 | `basic:project:delete` | 删除项目 |
| 项目管理 | `basic:project:import` | 导入项目 |
| 项目管理 | `basic:project:export` | 导出项目 |
| 供应商管理 | `basic:supplier:view` | 查看供应商 |
| 供应商管理 | `basic:supplier:create` | 新增供应商 |
| 供应商管理 | `basic:supplier:edit` | 编辑供应商 |
| 供应商管理 | `basic:supplier:delete` | 删除供应商 |
| 供应商管理 | `basic:supplier:import` | 导入供应商 |
| 供应商管理 | `basic:supplier:export` | 导出供应商 |
| 物资分类 | `basic:category:view` | 查看物资分类 |
| 物资分类 | `basic:category:create` | 新增分类 |
| 物资分类 | `basic:category:edit` | 编辑分类 |
| 物资分类 | `basic:category:delete` | 删除分类 |
| 物资分类 | `basic:category:import` | 导入分类 |
| 物资分类 | `basic:category:export` | 导出分类 |
| 常用材料表 | `basic:material:view` | 查看材料表 |
| 常用材料表 | `basic:material:create` | 新增材料 |
| 常用材料表 | `basic:material:edit` | 编辑材料 |
| 常用材料表 | `basic:material:delete` | 删除材料 |
| 常用材料表 | `basic:material:import` | 导入材料 |
| 常用材料表 | `basic:material:export` | 导出材料 |
| 用料单位 | `basic:unit:view` | 查看单位 |
| 用料单位 | `basic:unit:create` | 新增单位 |
| 用料单位 | `basic:unit:edit` | 编辑单位 |
| 用料单位 | `basic:unit:delete` | 删除单位 |
| 工号管理 | `basic:work_number:view` | 查看工号 |
| 工号管理 | `basic:work_number:create` | 新增工号 |
| 工号管理 | `basic:work_number:edit` | 编辑工号 |
| 工号管理 | `basic:work_number:delete` | 删除工号 |
| 工号管理 | `basic:work_number:import` | 导入工号 |
| 工号管理 | `basic:work_number:export` | 导出工号 |

### 三、主数据管理 (master_data)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 公司物资主库 | `master:material:view` | 查看公司物资主库 |
| 公司物资主库 | `master:material:create` | 新增公司物资 |
| 公司物资主库 | `master:material:edit` | 编辑公司物资 |
| 公司物资主库 | `master:material:delete` | 删除公司物资 |
| 公司物资主库 | `master:material:import` | 导入公司物资 |
| 公司物资主库 | `master:material:export` | 导出公司物资 |
| 公司供应商主库 | `master:supplier:view` | 查看公司供应商 |
| 公司供应商主库 | `master:supplier:create` | 新增公司供应商 |
| 公司供应商主库 | `master:supplier:edit` | 编辑公司供应商 |
| 公司供应商主库 | `master:supplier:delete` | 删除公司供应商 |
| 公司供应商主库 | `master:supplier:import` | 导入公司供应商 |
| 公司供应商主库 | `master:supplier:export` | 导出公司供应商 |
| 公司物资分类 | `master:category:view` | 查看公司分类 |
| 公司物资分类 | `master:category:create` | 新增公司分类 |
| 公司物资分类 | `master:category:edit` | 编辑公司分类 |
| 公司物资分类 | `master:category:delete` | 删除公司分类 |

### 四、采购合同 (purchase_contract)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 采购申请 | `contract:purchase_requisition:view` | 查看采购申请 |
| 采购申请 | `contract:purchase_requisition:create` | 新增采购申请 |
| 采购申请 | `contract:purchase_requisition:edit` | 编辑采购申请 |
| 采购申请 | `contract:purchase_requisition:delete` | 删除采购申请 |
| 采购申请 | `contract:purchase_requisition:import` | 导入采购申请 |
| 采购申请 | `contract:purchase_requisition:export` | 导出采购申请 |
| 采购申请 | `contract:purchase_requisition:approve` | 审批采购申请 |
| 合同台账 | `contract:list:view` | 查看合同列表 |
| 合同台账 | `contract:list:create` | 新增合同 |
| 合同台账 | `contract:list:edit` | 编辑合同 |
| 合同台账 | `contract:list:delete` | 删除合同 |
| 合同台账 | `contract:list:import` | 导入合同 |
| 合同台账 | `contract:list:export` | 导出合同 |
| 合同台账 | `contract:list:print` | 打印合同 |
| 对账管理 | `contract:reconciliation:view` | 查看对账 |
| 对账管理 | `contract:reconciliation:create` | 新增对账 |
| 对账管理 | `contract:reconciliation:edit` | 编辑对账 |
| 对账管理 | `contract:reconciliation:delete` | 删除对账 |
| 对账管理 | `contract:reconciliation:import` | 导入对账 |
| 对账管理 | `contract:reconciliation:export` | 导出对账 |
| 发票台账 | `contract:invoices:view` | 查看发票 |
| 发票台账 | `contract:invoices:create` | 新增发票 |
| 发票台账 | `contract:invoices:edit` | 编辑发票 |
| 发票台账 | `contract:invoices:delete` | 删除发票 |
| 发票台账 | `contract:invoices:export` | 导出发票 |
| 付款申请 | `contract:payment_application:view` | 查看付款申请 |
| 付款申请 | `contract:payment_application:create` | 新增付款申请 |
| 付款申请 | `contract:payment_application:edit` | 编辑付款申请 |
| 付款申请 | `contract:payment_application:delete` | 删除付款申请 |
| 付款申请 | `contract:payment_application:approve` | 审批付款申请 |
| 付款台账 | `contract:payments:view` | 查看付款台账 |
| 付款台账 | `contract:payments:export` | 导出付款台账 |
| 价格方案 | `contract:price_formula:view` | 查看价格方案 |
| 价格方案 | `contract:price_formula:create` | 新增价格方案 |
| 价格方案 | `contract:price_formula:edit` | 编辑价格方案 |
| 价格方案 | `contract:price_formula:delete` | 删除价格方案 |

### 五、库存管理 (inventory)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 入库管理 | `stock:in:view` | 查看入库单 |
| 入库管理 | `stock:in:create` | 新增入库单 |
| 入库管理 | `stock:in:edit` | 编辑入库单 |
| 入库管理 | `stock:in:delete` | 删除入库单 |
| 入库管理 | `stock:in:import` | 导入入库单 |
| 入库管理 | `stock:in:export` | 导出入库单 |
| 入库管理 | `stock:in:approve` | 审批入库单 |
| 入库管理 | `stock:in:print` | 打印入库单 |
| 出库管理 | `stock:out:view` | 查看出库单 |
| 出库管理 | `stock:out:create` | 新增出库单 |
| 出库管理 | `stock:out:edit` | 编辑出库单 |
| 出库管理 | `stock:out:delete` | 删除出库单 |
| 出库管理 | `stock:out:import` | 导入出库单 |
| 出库管理 | `stock:out:export` | 导出出库单 |
| 出库管理 | `stock:out:approve` | 审批出库单 |
| 出库管理 | `stock:out:print` | 打印出库单 |
| 库存查询 | `stock:query:view` | 查看库存 |
| 库存查询 | `stock:query:export` | 导出库存 |
| 库存盘点 | `stock:check:view` | 查看盘点 |
| 库存盘点 | `stock:check:create` | 新增盘点 |
| 库存盘点 | `stock:check:edit` | 编辑盘点 |
| 库存盘点 | `stock:check:delete` | 删除盘点 |
| 库存盘点 | `stock:check:approve` | 审批盘点 |
| 库存盘点 | `stock:check:export` | 导出盘点 |
| 项目调拨 | `stock:transfer:view` | 查看调拨 |
| 项目调拨 | `stock:transfer:create` | 新增调拨 |
| 项目调拨 | `stock:transfer:edit` | 编辑调拨 |
| 项目调拨 | `stock:transfer:delete` | 删除调拨 |
| 项目调拨 | `stock:transfer:approve` | 审批调拨 |
| 批次管理 | `stock:batch:view` | 查看批次 |
| 批次管理 | `stock:batch:create` | 新增批次 |
| 批次管理 | `stock:batch:edit` | 编辑批次 |
| 批次管理 | `stock:batch:delete` | 删除批次 |
| 到期提醒 | `stock:expiry:view` | 查看到期提醒 |
| 物资报废 | `stock:scrap:view` | 查看报废 |
| 物资报废 | `stock:scrap:create` | 新增报废 |
| 物资报废 | `stock:scrap:edit` | 编辑报废 |
| 物资报废 | `stock:scrap:delete` | 删除报废 |
| 物资报废 | `stock:scrap:approve` | 审批报废 |
| 期末结账 | `stock:period_close:view` | 查看期末结账 |
| 期末结账 | `stock:period_close:create` | 执行结账 |

### 六、周转材管理 (turnover_material)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 周转材台账 | `turnover:material:view` | 查看周转材 |
| 周转材台账 | `turnover:material:create` | 新增周转材 |
| 周转材台账 | `turnover:material:edit` | 编辑周转材 |
| 周转材台账 | `turnover:material:delete` | 删除周转材 |
| 周转材台账 | `turnover:material:import` | 导入周转材 |
| 周转材台账 | `turnover:material:export` | 导出周转材 |
| 领用归还 | `turnover:record:view` | 查看领用记录 |
| 领用归还 | `turnover:record:create` | 新增领用/归还 |
| 领用归还 | `turnover:record:edit` | 编辑领用记录 |
| 领用归还 | `turnover:record:delete` | 删除领用记录 |
| 租赁费结算 | `turnover:rental:view` | 查看租赁费 |
| 租赁费结算 | `turnover:rental:create` | 新增租赁费 |
| 租赁费结算 | `turnover:rental:edit` | 编辑租赁费 |
| 租赁费结算 | `turnover:rental:export` | 导出租赁费 |

### 七、设备管理 (equipment)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 设备台账 | `equipment:list:view` | 查看设备 |
| 设备台账 | `equipment:list:create` | 新增设备 |
| 设备台账 | `equipment:list:edit` | 编辑设备 |
| 设备台账 | `equipment:list:delete` | 删除设备 |
| 设备台账 | `equipment:list:import` | 导入设备 |
| 设备台账 | `equipment:list:export` | 导出设备 |
| 租赁结算 | `equipment:rent:view` | 查看租赁结算 |
| 租赁结算 | `equipment:rent:create` | 新增租赁结算 |
| 租赁结算 | `equipment:rent:edit` | 编辑租赁结算 |
| 租赁结算 | `equipment:rent:export` | 导出租赁结算 |
| 设备折旧 | `equipment:depreciation:view` | 查看折旧 |
| 设备折旧 | `equipment:depreciation:create` | 计算折旧 |
| 设备折旧 | `equipment:depreciation:export` | 导出折旧 |
| 巡检计划 | `equipment:inspection_plan:view` | 查看巡检计划 |
| 巡检计划 | `equipment:inspection_plan:create` | 新增巡检计划 |
| 巡检计划 | `equipment:inspection_plan:edit` | 编辑巡检计划 |
| 巡检计划 | `equipment:inspection_plan:delete` | 删除巡检计划 |
| 巡检任务 | `equipment:inspection_task:view` | 查看巡检任务 |
| 巡检任务 | `equipment:inspection_task:create` | 新增巡检任务 |
| 巡检任务 | `equipment:inspection_task:edit` | 编辑巡检任务 |
| 巡检记录 | `equipment:inspection_record:view` | 查看巡检记录 |
| 巡检记录 | `equipment:inspection_record:create` | 新增巡检记录 |
| 巡检记录 | `equipment:inspection_record:edit` | 编辑巡检记录 |

### 八、行业工具 (industry_tools)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 商砼小票 | `tools:concrete:view` | 查看商砼小票 |
| 商砼小票 | `tools:concrete:create` | 新增商砼小票 |
| 商砼小票 | `tools:concrete:edit` | 编辑商砼小票 |
| 商砼小票 | `tools:concrete:delete` | 删除商砼小票 |
| 商砼小票 | `tools:concrete:import` | 导入商砼小票 |
| 商砼小票 | `tools:concrete:export` | 导出商砼小票 |
| 商砼对账 | `tools:concrete_reconcile:view` | 查看商砼对账 |
| 商砼对账 | `tools:concrete_reconcile:create` | 新增商砼对账 |
| 商砼对账 | `tools:concrete_reconcile:export` | 导出商砼对账 |
| 钢材换算 | `tools:steel:view` | 查看钢材换算 |
| 规格表管理 | `tools:steel_specs:view` | 查看规格表 |
| 规格表管理 | `tools:steel_specs:create` | 新增规格 |
| 规格表管理 | `tools:steel_specs:edit` | 编辑规格 |
| 规格表管理 | `tools:steel_specs:delete` | 删除规格 |
| 条码标签 | `tools:barcode:view` | 查看条码标签 |
| 条码标签 | `tools:barcode:create` | 生成条码 |
| 条码标签 | `tools:barcode:print` | 打印条码 |
| 批量打印 | `tools:barcode_batch:view` | 查看批量打印 |
| 批量打印 | `tools:barcode_batch:print` | 批量打印 |

### 九、统计报表 (report)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 入库统计 | `report:stock_in:view` | 查看入库统计 |
| 入库统计 | `report:stock_in:export` | 导出入库统计 |
| 出库统计 | `report:stock_out:view` | 查看出库统计 |
| 出库统计 | `report:stock_out:export` | 导出出库统计 |
| 物资动态表 | `report:movement:view` | 查看物资动态 |
| 物资动态表 | `report:movement:export` | 导出物资动态 |
| 收发存汇总表 | `report:receive_issue:view` | 查看收发存汇总 |
| 收发存汇总表 | `report:receive_issue:export` | 导出收发存汇总 |
| 物资明细账 | `report:ledger:view` | 查看明细账 |
| 物资明细账 | `report:ledger:export` | 导出明细账 |
| 工号成本统计 | `report:work_number_cost:view` | 查看工号成本 |
| 工号成本统计 | `report:work_number_cost:export` | 导出工号成本 |
| 供应商往来 | `report:supplier_ledger:view` | 查看供应商往来 |
| 供应商往来 | `report:supplier_ledger:export` | 导出供应商往来 |
| 分包扣款台账 | `report:subcontract:view` | 查看分包扣款 |
| 分包扣款台账 | `report:subcontract:export` | 导出分包扣款 |
| 浇筑部位统计 | `report:concrete_stats:view` | 查看浇筑统计 |
| 高级分析 | `report:advanced:view` | 查看高级分析 |

### 十、系统管理 (system)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 用户管理 | `system:user:view` | 查看用户 |
| 用户管理 | `system:user:create` | 新增用户 |
| 用户管理 | `system:user:edit` | 编辑用户 |
| 用户管理 | `system:user:delete` | 删除用户 |
| 用户管理 | `system:user:import` | 导入用户 |
| 用户管理 | `system:user:export` | 导出用户 |
| 组织架构 | `system:dept:view` | 查看部门 |
| 组织架构 | `system:dept:create` | 新增部门 |
| 组织架构 | `system:dept:edit` | 编辑部门 |
| 组织架构 | `system:dept:delete` | 删除部门 |
| 角色管理 | `system:role:view` | 查看角色 |
| 角色管理 | `system:role:create` | 新增角色 |
| 角色管理 | `system:role:edit` | 编辑角色 |
| 角色管理 | `system:role:delete` | 删除角色 |
| 菜单管理 | `system:menu:view` | 查看菜单 |
| 菜单管理 | `system:menu:create` | 新增菜单 |
| 菜单管理 | `system:menu:edit` | 编辑菜单 |
| 菜单管理 | `system:menu:delete` | 删除菜单 |
| 基础信息管理 | `system:dict:view` | 查看字典 |
| 基础信息管理 | `system:dict:create` | 新增字典 |
| 基础信息管理 | `system:dict:edit` | 编辑字典 |
| 基础信息管理 | `system:dict:delete` | 删除字典 |
| 审批流程管理 | `system:approval_flow:view` | 查看审批流程 |
| 审批流程管理 | `system:approval_flow:create` | 新增流程 |
| 审批流程管理 | `system:approval_flow:edit` | 编辑流程 |
| 审批流程管理 | `system:approval_flow:delete` | 删除流程 |
| 批次分类配置 | `system:batch_category:view` | 查看批次配置 |
| 批次分类配置 | `system:batch_category:create` | 新增配置 |
| 批次分类配置 | `system:batch_category:edit` | 编辑配置 |
| 系统配置 | `system:config:view` | 查看系统配置 |
| 系统配置 | `system:config:edit` | 编辑系统配置 |
| 公告管理 | `system:announcement:view` | 查看公告 |
| 公告管理 | `system:announcement:create` | 新增公告 |
| 公告管理 | `system:announcement:edit` | 编辑公告 |
| 公告管理 | `system:announcement:delete` | 删除公告 |
| 通知配置 | `system:notify:view` | 查看通知配置 |
| 通知配置 | `system:notify:edit` | 编辑通知配置 |
| AI配置 | `system:ai_config:view` | 查看AI配置 |
| AI配置 | `system:ai_config:edit` | 编辑AI配置 |
| 附件管理 | `system:attachment:view` | 查看附件 |
| 附件管理 | `system:attachment:delete` | 删除附件 |
| 数据备份 | `system:backup:view` | 查看备份 |
| 数据备份 | `system:backup:create` | 创建备份 |
| 数据回收站 | `system:recycle_bin:view` | 查看回收站 |
| 数据回收站 | `system:recycle_bin:restore` | 恢复数据 |
| 数据回收站 | `system:recycle_bin:delete` | 永久删除 |
| 项目归档 | `system:archive:view` | 查看归档 |
| 项目归档 | `system:archive:create` | 归档项目 |
| 数据库迁移 | `system:db_migration:view` | 查看迁移 |
| 数据库迁移 | `system:db_migration:execute` | 执行迁移 |
| 操作日志 | `system:audit_log:view` | 查看操作日志 |
| 登录日志 | `system:login_log:view` | 查看登录日志 |
| 在线用户 | `system:online_user:view` | 查看在线用户 |
| 在线用户 | `system:online_user:kick` | 踢除用户 |
| 错误日志 | `system:error_log:view` | 查看错误日志 |
| AI调用日志 | `system:ai_log:view` | 查看AI日志 |

### 十一、帮助中心 (help)
| 功能 | 权限标识 | 说明 |
|------|----------|------|
| 使用说明 | `help:guide:view` | 查看使用说明 |
| 移动端录入 | `help:mobile:view` | 查看移动端说明 |

## 权限点统计
- **总菜单数**: 89个
- **总权限点**: 89 × 8 = 712个（理论最大值）
- **实际权限点**: 根据菜单功能特性，部分菜单不支持所有操作

## 权限点生成规则
1. 目录级（catalog）：不生成权限点，仅作为菜单分组
2. 菜单级（menu）：为每个菜单生成对应操作的权限点
3. 按钮级（button）：已废弃，操作权限通过 role_id + menu_id + operation 组合实现

## 注意事项
1. 权限标识必须全局唯一，禁止重复
2. 权限标识命名必须遵循 `模块:功能:操作` 规范
3. 新增模块或菜单时，必须同步更新此清单
4. 删除菜单时，必须同步删除相关权限点及角色权限关联