-- 版本 V3：综合优化更新
-- 添加AI视觉识别模型配置项
INSERT INTO system_config (config_key, config_value, description) 
SELECT 'ai_vision_model', 'doubao-vision-pro-32k', 'AI视觉识别模型名称' 
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE config_key = 'ai_vision_model');

-- 补充字典数据：入库类型增加退库入库
INSERT INTO sys_dict_item (dict_type_id, item_label, item_value, sort_order, is_active)
SELECT dt.id, '退库入库', '退库入库', (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM sys_dict_item WHERE dict_type_id = dt.id), 1
FROM sys_dict_type dt
WHERE dt.dict_type = 'stockin_type'
AND NOT EXISTS (SELECT 1 FROM sys_dict_item WHERE dict_type_id = dt.id AND item_value = '退库入库');

-- 补充字典数据：审批通过规则
INSERT INTO sys_dict_type (dict_type, dict_name, is_active)
SELECT 'approval_rule', '审批通过规则', 1
WHERE NOT EXISTS (SELECT 1 FROM sys_dict_type WHERE dict_type = 'approval_rule');

INSERT INTO sys_dict_item (dict_type_id, item_label, item_value, sort_order, is_active)
SELECT dt.id, '或签', 'or_sign', 0, 1
FROM sys_dict_type dt
WHERE dt.dict_type = 'approval_rule'
AND NOT EXISTS (SELECT 1 FROM sys_dict_item WHERE dict_type_id = dt.id AND item_value = 'or_sign');

INSERT INTO sys_dict_item (dict_type_id, item_label, item_value, sort_order, is_active)
SELECT dt.id, '会签', 'and_sign', 1, 1
FROM sys_dict_type dt
WHERE dt.dict_type = 'approval_rule'
AND NOT EXISTS (SELECT 1 FROM sys_dict_item WHERE dict_type_id = dt.id AND item_value = 'and_sign');

-- 补充字典数据：材料来源
INSERT INTO sys_dict_type (dict_type, dict_name, is_active)
SELECT 'material_source', '材料来源', 1
WHERE NOT EXISTS (SELECT 1 FROM sys_dict_type WHERE dict_type = 'material_source');

INSERT INTO sys_dict_item (dict_type_id, item_label, item_value, sort_order, is_active)
SELECT dt.id, '自购', 'self_purchase', 0, 1
FROM sys_dict_type dt
WHERE dt.dict_type = 'material_source'
AND NOT EXISTS (SELECT 1 FROM sys_dict_item WHERE dict_type_id = dt.id AND item_value = 'self_purchase');

INSERT INTO sys_dict_item (dict_type_id, item_label, item_value, sort_order, is_active)
SELECT dt.id, '甲供', 'supplier_provided', 1, 1
FROM sys_dict_type dt
WHERE dt.dict_type = 'material_source'
AND NOT EXISTS (SELECT 1 FROM sys_dict_item WHERE dict_type_id = dt.id AND item_value = 'supplier_provided');