"""AI服务 - 豆包大模型接入"""
import requests
import json
import time
import random
import logging
from flask_login import current_user
from app import db
from app.models import AICallLog
from app.utils import get_config

logger = logging.getLogger(__name__)


class AIService:
    """AI助手服务类"""
    
    def __init__(self):
        self.enabled = False
        self.api_key = ''
        self.model = 'doubao-pro-32k'
        self.base_url = 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'
        self.max_tokens = 2000
        self._load_config()
    
    def _load_config(self):
        """加载配置"""
        try:
            self.enabled = str(get_config('ai_enabled', 'false')).lower() == 'true'
            self.api_key = get_config('ai_api_key', '')
            self.model = get_config('ai_model', 'doubao-pro-32k')
            self.base_url = get_config('ai_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions')
            try:
                self.max_tokens = int(get_config('ai_max_tokens', '2000'))
            except (ValueError, TypeError):
                logger.warning("ai_max_tokens 配置无效,使用默认值 2000")
                self.max_tokens = 2000
        except Exception as e:
            logger.warning(f"AI 配置加载失败,已禁用 AI 服务: {e}")
            self.enabled = False
    
    def is_enabled(self):
        return self.enabled
    
    def call(self, messages, module='chat'):
        """调用豆包API"""
        if not self.enabled:
            return None, 'AI助手未启用'
        if not self.api_key:
            return None, '请先配置API Key'
        
        start = time.time()
        success = True
        error_msg = None
        response_text = None
        input_tokens = 0
        output_tokens = 0
        
        try:
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}'
            }
            
            payload = {
                'model': self.model,
                'messages': messages,
                'max_tokens': self.max_tokens,
                'temperature': 0.7,
                'stream': False
            }
            
            # 总超时上限60秒,单次请求15秒,最多重试3次,指数退避
            total_deadline = time.time() + 60
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    remaining = max(5, int(total_deadline - time.time()))
                    timeout = min(15, remaining)
                    resp = requests.post(self.base_url, headers=headers, json=payload, timeout=timeout)
                    # 4xx 不可恢复错误,直接返回
                    if 400 <= resp.status_code < 500:
                        error_msg = f'AI请求参数错误(HTTP {resp.status_code}): {resp.text[:200]}'
                        success = False
                        logger.warning(f'AI 4xx 不可重试: {error_msg}')
                        break
                    resp.raise_for_status()
                    result = resp.json()

                    if 'choices' in result and result['choices']:
                        response_text = result['choices'][0]['message']['content']
                    if 'usage' in result:
                        input_tokens = result['usage'].get('prompt_tokens', 0)
                        output_tokens = result['usage'].get('completion_tokens', 0)
                    break
                except requests.exceptions.Timeout:
                    if attempt < max_attempts - 1 and time.time() < total_deadline:
                        # 指数退避 + 抖动
                        backoff = min(2 ** attempt + random.random(), 5)
                        logger.warning(f'AI 请求超时,{backoff:.1f}s 后重试(第{attempt+1}次)')
                        time.sleep(backoff)
                        continue
                    error_msg = 'AI 请求超时,请稍后重试'
                    success = False
                except requests.exceptions.ConnectionError as e:
                    if attempt < max_attempts - 1 and time.time() < total_deadline:
                        backoff = min(2 ** attempt + random.random(), 5)
                        logger.warning(f'AI 连接错误,{backoff:.1f}s 后重试: {e}')
                        time.sleep(backoff)
                        continue
                    error_msg = f'AI 服务连接失败: {e}'
                    success = False
                except requests.exceptions.RequestException as e:
                    if attempt < max_attempts - 1 and time.time() < total_deadline:
                        backoff = min(2 ** attempt + random.random(), 5)
                        logger.warning(f'AI 请求异常,{backoff:.1f}s 后重试: {e}')
                        time.sleep(backoff)
                        continue
                    error_msg = str(e)
                    success = False
            
        except Exception as e:
            success = False
            error_msg = str(e)
        
        cost_time = int((time.time() - start) * 1000)
        
        self._log(module, messages, response_text, input_tokens, output_tokens, 
                  cost_time, success, error_msg)
        
        return response_text, error_msg
    
    def _log(self, module, prompt, response, input_tokens, output_tokens, 
             cost_time, success, error_msg):
        """记录调用日志"""
        try:
            prompt_str = json.dumps(prompt, ensure_ascii=False) if isinstance(prompt, list) else str(prompt)
            log = AICallLog(
                user_id=current_user.id if current_user.is_authenticated else None,
                username=current_user.username if current_user.is_authenticated else None,
                module=module,
                prompt=prompt_str[:5000],
                response=response[:5000] if response else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_time=cost_time,
                success=success,
                error_msg=error_msg[:200] if error_msg else None
            )
            db.session.add(log)
            db.session.commit()
        except Exception:
            db.session.rollback()
    
    def chat(self, user_message, context=None):
        """智能问答"""
        messages = []
        
        if context:
            messages.extend(context[-5:])
        
        system_prompt = """你是物资设备管理系统的智能助手。请根据提供的数据回答用户问题。
        
规则：
1. 如果没有相关数据，明确告知用户没有找到相关信息
2. 回答要简洁明了，使用中文
3. 金额数据使用千分位格式
4. 只回答当前项目的数据
5. 如果用户问题不明确，礼貌地询问补充信息

可用数据字段：
- 库存：物资名称、规格、数量、单位、分类
- 供应商：名称、评级、供货金额、交货率
- 入库：日期、供应商、金额、物资明细
- 出库：日期、领料单位、金额、物资明细
- 合同：金额、入库进度、付款进度、供应商
- 采购申请：状态、审批进度
- 盘点：盘盈盘亏数量、差异金额
- 调拨：调出/调入项目、物资明细
- 周转材：在租数量、租赁费
- 设备：状态、维保到期时间、维修中数量"""
        
        messages.insert(0, {'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': user_message})
        
        return self.call(messages, 'chat')
    
    def analyze_report(self, data, report_type):
        """报表智能分析"""
        system_prompt = f"""你是物资设备管理系统的报表分析助手。请分析{report_type}数据并生成专业分析报告。
        
分析内容包括：
1. 本期核心数据摘要（总金额、总数量）
2. 排名Top3的物资及占比
3. 异常波动提醒（激增或骤减的物资）
4. 管理建议

数据格式：
{json.dumps(data, ensure_ascii=False, default=str)[:3000]}"""
        
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': f'请分析这份{report_type}数据，生成分析报告'}
        ]
        
        return self.call(messages, 'analysis')
    
    def parse_input(self, user_input):
        """智能录入解析"""
        system_prompt = """你是物资设备管理系统的智能录入助手。请解析用户输入的自然语言，提取关键信息并按指定格式输出。

用户可能输入的场景：
1. 入库："今天从钢铁供应商A进了10吨HRB400带肋钢筋，单价4200"
2. 出库："木工班组领了200张模板"
3. 采购申请："申请采购水泥50吨，下周三要"

输出格式（JSON）：
{
    "type": "stock_in/stock_out/purchase_requisition",
    "supplier": "供应商名称",
    "usage_unit": "领料单位名称",
    "items": [
        {"material": "物资名称", "specification": "规格", "quantity": 数量, "unit_price": 单价}
    ],
    "date": "日期（YYYY-MM-DD）",
    "demand_date": "需求日期（采购申请用）"
}

注意：
- 数量和单价必须是数字
- 无法识别的字段留空或null
- 如果类型不明确，type设为null"""
        
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': f'解析：{user_input}'}
        ]
        
        result, error = self.call(messages, 'input')
        if result:
            try:
                return json.loads(result), None
            except json.JSONDecodeError:
                return None, '解析结果格式错误'
        return None, error
    
    def summarize_document(self, doc_type, data):
        """单据智能摘要"""
        system_prompt = f"""你是物资设备管理系统的单据摘要助手。请根据{doc_type}数据生成一句话核心摘要。

数据：
{json.dumps(data, ensure_ascii=False, default=str)[:2000]}

要求：
1. 生成一句话摘要，不超过50字
2. 包含关键信息：金额、状态、进度等
3. 语言简洁明了"""
        
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': f'生成{doc_type}摘要'}
        ]
        
        return self.call(messages, 'summary')
    
    def generate_opinion(self, doc_type, data, action='approve'):
        """审批意见辅助生成"""
        system_prompt = f"""你是物资设备管理系统的审批助手。请根据{doc_type}数据生成审批意见。

数据：
{json.dumps(data, ensure_ascii=False, default=str)[:2000]}

要求：
1. 如果action是approve，生成同意意见
2. 如果action是reject，生成驳回意见（需说明原因）
3. 意见简洁专业，不超过30字"""
        
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': f'生成{"同意" if action == "approve" else "驳回"}意见'}
        ]
        
        return self.call(messages, 'approval')


ai_service = None


def get_ai_service():
    """获取 AI 服务实例(懒加载,避免在应用上下文外初始化)"""
    global ai_service
    if ai_service is None:
        ai_service = AIService()
    # 每次调用时重新加载配置(支持运行时修改)
    ai_service._load_config()
    return ai_service


def get_ai_config():
    """获取AI配置"""
    return {
        'enabled': get_config('ai_enabled', 'false') == 'true',
        'api_key': get_config('ai_api_key', ''),
        'model': get_config('ai_model', 'doubao-pro-32k'),
        'base_url': get_config('ai_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'),
        'max_tokens': int(get_config('ai_max_tokens', '2000'))
    }


def save_ai_config(config):
    """保存AI配置"""
    from app.models import SystemConfig
    
    configs = [
        ('ai_enabled', config.get('enabled', 'false')),
        ('ai_api_key', config.get('api_key', '')),
        ('ai_model', config.get('model', 'doubao-pro-32k')),
        ('ai_base_url', config.get('base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions')),
        ('ai_max_tokens', str(config.get('max_tokens', 2000)))
    ]
    
    for key, value in configs:
        c = SystemConfig.query.filter_by(config_key=key).first()
        if c:
            c.config_value = value
        else:
            db.session.add(SystemConfig(config_key=key, config_value=value))
    
    db.session.commit()
    
    from app.utils import ConfigCache
    ConfigCache.clear()
