-- 版本 V4：工号管理分级改造 - 支持分部工程+分项工程两级结构

-- 添加 parent_id 字段（自引用关联）
ALTER TABLE work_numbers ADD COLUMN parent_id INTEGER;
ALTER TABLE work_numbers ADD CONSTRAINT fk_work_number_parent FOREIGN KEY (parent_id) REFERENCES work_numbers(id) ON DELETE CASCADE;

-- 添加 remark 字段
ALTER TABLE work_numbers ADD COLUMN remark VARCHAR(512);

-- 历史数据升级：将已有的工号数据升级为分项工程，同时为每个工号创建对应的分部工程
-- 1. 创建临时表存储映射关系
CREATE TABLE IF NOT EXISTS temp_work_number_map (
    old_id INTEGER PRIMARY KEY,
    division_id INTEGER,
    item_id INTEGER
);

-- 2. 为每个已有工号创建分部工程（使用原来的分部名称）
INSERT INTO work_numbers (project_id, parent_id, code, division_name, team_name, picker, remark, created_at)
SELECT 
    project_id, 
    NULL, 
    'GH' || printf('%04d', (ROW_NUMBER() OVER (ORDER BY id))),
    COALESCE(division_name, '未命名分部'),
    NULL,
    NULL,
    NULL,
    created_at
FROM work_numbers 
WHERE parent_id IS NULL;

-- 3. 更新原有工号为分项工程，关联到新创建的分部工程
-- 先获取分部工程的最大ID
WITH max_div AS (
    SELECT MAX(id) as max_id FROM work_numbers WHERE parent_id IS NULL
)
UPDATE work_numbers 
SET parent_id = (SELECT max_id - (SELECT COUNT(*) FROM work_numbers WHERE id > work_numbers.id AND parent_id IS NULL) FROM max_div)
WHERE parent_id IS NULL AND (SELECT COUNT(*) FROM work_numbers WHERE parent_id IS NULL) > (SELECT COUNT(*) FROM work_numbers WHERE parent_id IS NOT NULL);

-- 清理临时表
DROP TABLE IF EXISTS temp_work_number_map;

-- 创建索引优化查询
CREATE INDEX IF NOT EXISTS idx_work_numbers_parent_id ON work_numbers(parent_id);
