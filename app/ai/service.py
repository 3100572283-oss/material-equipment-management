"""AI服务 - 豆包大模型接入（统一中台版本）

所有业务模块只能通过 get_ai_service() 获取实例，禁止直接调用 API。
调用规范：
- 文本场景：ai_service.chat_with_scene(scene_code, user_message, extra_context=None)
- 视觉场景：ai_service.recognize_with_scene(scene_code, image_base64, extra_prompt=None)
- 语音场景：ai_service.speech_to_text(audio_data, scene_code='speech:to_text')
- 结构化生成：ai_service.generate_structured(doc_type, data, template=None)
"""
import requests
import json
import time
import random
import logging
import re
from flask import request
from flask_login import current_user
from app import db
from app.models import AICallLog, AIScenePrompt
from app.utils import get_config

logger = logging.getLogger(__name__)


class AIService:
    """AI统一中台服务类

    所有AI能力收敛到本类，业务模块只能通过场景编码调用，禁止直接拼接Prompt。
    """

    # 场景Prompt缓存（key: scene_code, value: AIScenePrompt对象）
    _scene_cache = {}
    _scene_cache_time = 0
    _scene_cache_ttl = 60  # 缓存60秒

    # 成本单价（元/千token，默认值，可在配置中覆盖）
    DEFAULT_TEXT_PRICE = 0.003     # 文本模型每千token约 0.003元
    DEFAULT_VISION_PRICE = 0.01    # 视觉模型每千token约 0.01元

    def __init__(self):
        self.enabled = False
        self.api_key = ''
        self.model = 'doubao-1.5-pro-32k'
        self.base_url = 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'
        self.max_tokens = 2000
        self.vision_enabled = False
        self.vision_model = 'doubao-1.5-vision-pro'
        self.vision_base_url = 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'
        self.vision_max_tokens = 2000
        self.speech_enabled = False
        self.speech_model = 'doubao-tts'
        self.speech_base_url = 'https://ark.cn-beijing.volces.com/api/v3/audio/transcriptions'
        self.text_price_per_ktoken = self.DEFAULT_TEXT_PRICE
        self.vision_price_per_ktoken = self.DEFAULT_VISION_PRICE
        self._load_config()

    def _load_config(self):
        """加载配置"""
        try:
            self.enabled = str(get_config('ai_enabled', 'false')).lower() == 'true'
            self.api_key = get_config('ai_api_key', '')
            self.model = get_config('ai_model', 'doubao-1.5-pro-32k')
            self.base_url = get_config('ai_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions')
            try:
                self.max_tokens = int(get_config('ai_max_tokens', '2000'))
            except (ValueError, TypeError):
                logger.warning("ai_max_tokens 配置无效,使用默认值 2000")
                self.max_tokens = 2000

            # 视觉模型配置
            self.vision_enabled = str(get_config('ai_vision_enabled', 'false')).lower() == 'true'
            self.vision_model = get_config('ai_vision_model', 'doubao-1.5-vision-pro')
            self.vision_base_url = get_config('ai_vision_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions')
            try:
                self.vision_max_tokens = int(get_config('ai_vision_max_tokens', '2000'))
            except (ValueError, TypeError):
                self.vision_max_tokens = 2000

            # 语音配置
            self.speech_enabled = str(get_config('ai_speech_enabled', 'false')).lower() == 'true'
            self.speech_model = get_config('ai_speech_model', 'doubao-speech')
            self.speech_base_url = get_config('ai_speech_base_url', 'https://ark.cn-beijing.volces.com/api/v3/audio/transcriptions')

            # 成本单价
            try:
                self.text_price_per_ktoken = float(get_config('ai_text_price', str(self.DEFAULT_TEXT_PRICE)))
            except (ValueError, TypeError):
                pass
            try:
                self.vision_price_per_ktoken = float(get_config('ai_vision_price', str(self.DEFAULT_VISION_PRICE)))
            except (ValueError, TypeError):
                pass
        except Exception as e:
            logger.warning(f"AI 配置加载失败,已禁用 AI 服务: {e}")
            self.enabled = False

    # ================= 状态判断 =================

    def is_enabled(self):
        return self.enabled

    def is_vision_enabled(self):
        return self.enabled and self.vision_enabled

    def is_speech_enabled(self):
        return self.enabled and self.speech_enabled

    def is_scene_enabled(self, scene_code):
        """判断场景是否启用"""
        scene = self._get_scene(scene_code)
        return scene is not None and scene.is_enabled

    # ================= 场景Prompt管理 =================

    def _get_scene(self, scene_code):
        """获取场景配置（带缓存）"""
        now = time.time()
        if (now - self._scene_cache_time) > self._scene_cache_ttl or scene_code not in self._scene_cache:
            self._refresh_scene_cache()
        return self._scene_cache.get(scene_code)

    def _refresh_scene_cache(self):
        """刷新场景缓存"""
        try:
            scenes = AIScenePrompt.query.all()
            self._scene_cache = {s.scene_code: s for s in scenes}
            self._scene_cache_time = time.time()
        except Exception as e:
            logger.warning(f"刷新AI场景Prompt缓存失败: {e}")

    def invalidate_scene_cache(self):
        """手动失效场景缓存（后台修改配置后调用）"""
        self._scene_cache.clear()
        self._scene_cache_time = 0

    def get_all_scenes(self):
        """获取所有场景配置列表"""
        self._refresh_scene_cache()
        return list(self._scene_cache.values())

    # ================= 核心：统一文本调用 =================

    def call_with_scene(self, scene_code, user_message, extra_context=None, parse_json=False):
        """统一文本场景调用入口

        Args:
            scene_code: 场景编码（对应 ai_scene_prompt.scene_code）
            user_message: 用户输入消息
            extra_context: 额外上下文数据（dict或str），会拼接到消息中
            parse_json: 是否自动解析JSON结果

        Returns:
            (result_data, error_msg): parse_json=True时result_data为dict，否则为str
        """
        scene = self._get_scene(scene_code)
        if not scene:
            return None, f'场景不存在: {scene_code}'
        if not scene.is_enabled:
            return None, f'场景已禁用: {scene.scene_name}'

        if not self.enabled:
            return None, 'AI助手未启用'

        messages = []
        messages.append({'role': 'system', 'content': scene.system_prompt})
        if scene.output_format:
            messages.append({'role': 'system', 'content': f'输出格式要求：{scene.output_format}'})

        if extra_context:
            if isinstance(extra_context, dict):
                ctx_str = json.dumps(extra_context, ensure_ascii=False, default=str)
            else:
                ctx_str = str(extra_context)
            messages.append({'role': 'user', 'content': f'参考数据：{ctx_str[:3000]}'})

        messages.append({'role': 'user', 'content': user_message})

        response_text, error = self._call_text_api(messages, scene_code)

        if error or not response_text:
            return None, error

        if parse_json:
            return self._parse_json_result(response_text), None

        return response_text, None

    def _call_text_api(self, messages, scene_code):
        """调用文本模型API（内部方法，带重试和日志）"""
        if not self.enabled or not self.api_key:
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
            total_deadline = time.time() + 60
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    remaining = max(5, int(total_deadline - time.time()))
                    timeout = min(15, remaining)
                    resp = requests.post(self.base_url, headers=headers, json=payload, timeout=timeout)
                    if 400 <= resp.status_code < 500:
                        error_msg = f'AI请求参数错误(HTTP {resp.status_code}): {resp.text[:200]}'
                        success = False
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
        self._log_call(
            scene_code=scene_code,
            module='text',
            prompt=messages,
            response=response_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_time=cost_time,
            success=success,
            error_msg=error_msg,
            price_per_ktoken=self.text_price_per_ktoken,
        )
        return response_text, error_msg

    # ================= 核心：统一视觉调用 =================

    def recognize_with_scene(self, scene_code, image_base64, extra_prompt=None, parse_json=True):
        """统一视觉识别场景调用入口

        Args:
            scene_code: 场景编码
            image_base64: 图片base64（不含data:前缀）
            extra_prompt: 额外提示词
            parse_json: 是否自动解析JSON结果

        Returns:
            (result_data, error_msg)
        """
        scene = self._get_scene(scene_code)
        if not scene:
            return None, f'场景不存在: {scene_code}'
        if not scene.is_enabled:
            return None, f'场景已禁用: {scene.scene_name}'

        if not self.is_vision_enabled():
            return None, '视觉识别未启用'

        # 拼接prompt
        prompt = scene.system_prompt
        if scene.output_format:
            prompt += f'\n\n输出格式要求：{scene.output_format}'
        if extra_prompt:
            prompt += f'\n\n{extra_prompt}'

        response_text, error = self._call_vision_api(image_base64, prompt, scene_code)
        if error or not response_text:
            return None, error

        if parse_json:
            return self._parse_json_result(response_text), None
        return response_text, None

    def _call_vision_api(self, image_base64, prompt, scene_code):
        """调用视觉模型API（内部方法）"""
        if not self.is_vision_enabled() or not self.api_key:
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
            messages = [
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{image_base64}'}}
                    ]
                }
            ]
            payload = {
                'model': self.vision_model,
                'messages': messages,
                'max_tokens': self.vision_max_tokens
            }
            total_deadline = time.time() + 60
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    remaining = max(5, int(total_deadline - time.time()))
                    timeout = min(30, remaining)
                    resp = requests.post(self.vision_base_url, headers=headers, json=payload, timeout=timeout)
                    if 400 <= resp.status_code < 500:
                        error_msg = f'视觉识别请求参数错误(HTTP {resp.status_code}): {resp.text[:200]}'
                        success = False
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
                        backoff = min(2 ** attempt + random.random(), 5)
                        time.sleep(backoff)
                        continue
                    error_msg = '视觉识别请求超时,请稍后重试'
                    success = False
                except requests.exceptions.RequestException as e:
                    if attempt < max_attempts - 1 and time.time() < total_deadline:
                        backoff = min(2 ** attempt + random.random(), 5)
                        time.sleep(backoff)
                        continue
                    error_msg = f'视觉识别服务连接失败: {e}'
                    success = False
        except Exception as e:
            success = False
            error_msg = str(e)

        cost_time = int((time.time() - start) * 1000)
        self._log_call(
            scene_code=scene_code,
            module='vision',
            prompt=f'[image:{len(image_base64)//1000}KB] {prompt[:200]}',
            response=response_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_time=cost_time,
            success=success,
            error_msg=error_msg,
            price_per_ktoken=self.vision_price_per_ktoken,
        )
        return response_text, error_msg

    # ================= 核心：语音转文字 =================

    def speech_to_text(self, audio_data, scene_code='speech:to_text', audio_format='webm'):
        """语音转文字

        Args:
            audio_data: 音频文件二进制数据
            scene_code: 场景编码
            audio_format: 音频格式 webm/mp3/wav/m4a

        Returns:
            (text, error_msg)
        """
        scene = self._get_scene(scene_code)
        if not scene:
            return None, f'场景不存在: {scene_code}'
        if not scene.is_enabled:
            return None, f'场景已禁用: {scene.scene_name}'

        if not self.is_speech_enabled():
            return None, '语音识别未启用'
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
                'Authorization': f'Bearer {self.api_key}'
            }
            files = {
                'file': (f'audio.{audio_format}', audio_data, f'audio/{audio_format}')
            }
            data = {
                'model': self.speech_model,
                'language': 'zh',
            }
            resp = requests.post(
                self.speech_base_url,
                headers=headers,
                files=files,
                data=data,
                timeout=60
            )
            if resp.status_code == 200:
                result = resp.json()
                response_text = result.get('text', '') or result.get('transcript', '')
                # 估算token（按字符数，中文约1字符=1token）
                output_tokens = len(response_text) if response_text else 0
            else:
                error_msg = f'语音识别失败(HTTP {resp.status_code}): {resp.text[:200]}'
                success = False
        except Exception as e:
            success = False
            error_msg = f'语音识别服务异常: {e}'

        cost_time = int((time.time() - start) * 1000)
        self._log_call(
            scene_code=scene_code,
            module='speech',
            prompt=f'[audio:{len(audio_data)//1024}KB, format:{audio_format}]',
            response=response_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_time=cost_time,
            success=success,
            error_msg=error_msg,
            price_per_ktoken=self.text_price_per_ktoken,
        )
        return response_text, error_msg

    # ================= 核心：结构化生成 =================

    def generate_structured(self, doc_type, data, template=None):
        """结构化文档生成

        Args:
            doc_type: 文档类型（验收单/维保计划/分析报告/整改通知等）
            data: 业务数据 dict
            template: 可选的模板内容

        Returns:
            (content_dict, error_msg): 结构化内容字典
        """
        user_msg = f'请生成{doc_type}'
        if template:
            user_msg += f'，模板要求：{template}'

        result, error = self.call_with_scene(
            'structured:generate',
            user_msg,
            extra_context=data,
            parse_json=True,
        )
        return result, error

    # ================= 便捷方法（兼容旧代码） =================

    def chat(self, user_message, context=None):
        """智能对话（兼容旧代码，使用 text:chat 场景）"""
        messages = []
        if context:
            messages.extend(context[-5:])
        messages.append({'role': 'user', 'content': user_message})
        # 使用系统级调用方式（不走场景方法，保留上下文能力）
        response_text, error = self._call_text_api(messages, 'text:chat')
        return response_text, error

    def analyze_report(self, data, report_type):
        """报表智能分析"""
        return self.call_with_scene('text:report_analysis', f'请分析这份{report_type}数据', extra_context=data)

    def parse_input(self, user_input):
        """智能录入解析"""
        return self.call_with_scene('text:parse_input', f'解析：{user_input}', parse_json=True)

    def summarize_document(self, doc_type, data):
        """单据智能摘要"""
        return self.call_with_scene('text:doc_summary', f'生成{doc_type}摘要', extra_context=data)

    def generate_opinion(self, doc_type, data, action='approve'):
        """审批意见辅助生成"""
        return self.call_with_scene(
            'text:approval_opinion',
            f'生成{"同意" if action == "approve" else "驳回"}意见，action={action}',
            extra_context=data,
        )

    def recognize_business_license(self, image_base64):
        """识别营业执照"""
        return self.recognize_with_scene('vision:license', image_base64, parse_json=True)

    def recognize_invoice(self, image_base64):
        """识别发票"""
        return self.recognize_with_scene('vision:invoice', image_base64, parse_json=True)

    def recognize_receipt(self, image_base64):
        """识别收料小票/送货单"""
        return self.recognize_with_scene('vision:receipt', image_base64, parse_json=True)

    def recognize_concrete_ticket(self, image_base64):
        """识别商砼小票"""
        return self.recognize_with_scene('vision:concrete', image_base64, parse_json=True)

    def extract_text(self, image_base64):
        """通用图片文字提取"""
        return self.recognize_with_scene('vision:ocr', image_base64, parse_json=False)

    # ================= 工具方法 =================

    @staticmethod
    def _parse_json_result(text):
        """从AI返回文本中提取JSON"""
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
        return None

    def _log_call(self, scene_code, module, prompt, response,
                  input_tokens, output_tokens, cost_time, success, error_msg,
                  price_per_ktoken=0.003):
        """统一日志记录

        记录字段：用户、部门、场景、token、成本、耗时、成功状态、IP
        """
        try:
            from app.models import User
            total_tokens = input_tokens + output_tokens
            cost_amount = round(total_tokens * price_per_ktoken / 1000, 6) if total_tokens > 0 else 0

            prompt_str = json.dumps(prompt, ensure_ascii=False) if isinstance(prompt, (list, dict)) else str(prompt)
            resp_str = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False, default=str)

            dept_id = None
            if current_user.is_authenticated:
                user = User.query.get(current_user.id)
                if user and hasattr(user, 'dept_id'):
                    dept_id = user.dept_id

            ip = None
            try:
                ip = request.remote_addr
            except Exception:
                pass

            log = AICallLog(
                user_id=current_user.id if current_user.is_authenticated else None,
                username=current_user.username if current_user.is_authenticated else None,
                dept_id=dept_id,
                module=module,
                scene_code=scene_code,
                prompt=prompt_str[:5000],
                response=resp_str[:5000] if resp_str else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cost_amount=cost_amount,
                cost_time=cost_time,
                success=success,
                error_msg=error_msg[:500] if error_msg else None,
                ip_address=ip,
            )
            db.session.add(log)
            db.session.commit()
        except Exception as e:
            logger.warning(f"记录AI调用日志失败: {e}")
            db.session.rollback()


ai_service = None


def get_ai_service():
    """获取 AI 服务实例(懒加载，每次调用刷新配置和场景缓存)"""
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
        'model': get_config('ai_model', 'doubao-1.5-pro-32k'),
        'base_url': get_config('ai_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'),
        'max_tokens': int(get_config('ai_max_tokens', '2000')),
        'vision_enabled': get_config('ai_vision_enabled', 'false') == 'true',
        'vision_model': get_config('ai_vision_model', 'doubao-1.5-vision-pro'),
        'vision_base_url': get_config('ai_vision_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'),
        'vision_max_tokens': int(get_config('ai_vision_max_tokens', '2000')),
        'speech_enabled': get_config('ai_speech_enabled', 'false') == 'true',
        'speech_model': get_config('ai_speech_model', 'doubao-speech'),
        'speech_base_url': get_config('ai_speech_base_url', 'https://ark.cn-beijing.volces.com/api/v3/audio/transcriptions'),
        'text_price': float(get_config('ai_text_price', '0.003')),
        'vision_price': float(get_config('ai_vision_price', '0.01')),
    }


def save_ai_config(config):
    """保存AI配置"""
    from app.models import SystemConfig

    configs = [
        ('ai_enabled', config.get('enabled', 'false')),
        ('ai_api_key', config.get('api_key', '')),
        ('ai_model', config.get('model', 'doubao-1.5-pro-32k')),
        ('ai_base_url', config.get('base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions')),
        ('ai_max_tokens', str(config.get('max_tokens', 2000))),
        ('ai_vision_enabled', config.get('vision_enabled', 'false')),
        ('ai_vision_model', config.get('vision_model', 'doubao-1.5-vision-pro')),
        ('ai_vision_base_url', config.get('vision_base_url', 'https://ark.cn-beijing.volces.com/api/v3/chat/completions')),
        ('ai_vision_max_tokens', str(config.get('vision_max_tokens', 2000))),
        ('ai_speech_enabled', config.get('speech_enabled', 'false')),
        ('ai_speech_model', config.get('speech_model', 'doubao-speech')),
        ('ai_speech_base_url', config.get('speech_base_url', 'https://ark.cn-beijing.volces.com/api/v3/audio/transcriptions')),
        ('ai_text_price', str(config.get('text_price', 0.003))),
        ('ai_vision_price', str(config.get('vision_price', 0.01))),
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

    # 失效AI服务缓存
    global ai_service
    if ai_service:
        ai_service.invalidate_scene_cache()
