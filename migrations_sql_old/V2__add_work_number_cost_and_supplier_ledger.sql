-- 版本 V2：新增工号成本统计和供应商往来台账功能
-- 供应商表增加期初余额字段
ALTER TABLE suppliers ADD COLUMN opening_balance NUMERIC(18,2) DEFAULT 0;