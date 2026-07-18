# OCR 服务模块 - 可选依赖
# 如果未安装 paddleocr，将优雅降级为仅保存图片，不提供AI识别功能

import os
import re
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

logger = logging.getLogger(__name__)

# 线程安全的 OCR 引擎初始化锁
_ocr_lock = threading.Lock()
# OCR 调用线程池(串行化,避免 PaddleOCR 并发问题)
_ocr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ocr')
# OCR 单次调用超时(秒)
OCR_TIMEOUT = 20

# 尝试导入 paddleocr，如果失败则标记为不可用
OCR_AVAILABLE = False
ocr_engine = None

try:
    from paddleocr import PaddleOCR
    OCR_AVAILABLE = True
    logger.info("PaddleOCR 已加载，AI识别功能可用")
except ImportError:
    logger.warning("PaddleOCR 未安装，AI识别功能不可用，图片将仅保存不识别")


def init_ocr(use_gpu=False, lang='ch'):
    """
    初始化 OCR 引擎(线程安全)

    Args:
        use_gpu: 是否使用GPU加速
        lang: 语言，默认中文

    Returns:
        OCR引擎实例，如果不可用则返回None
    """
    global ocr_engine

    if not OCR_AVAILABLE:
        return None

    # 双重检查锁定,避免重复初始化
    if ocr_engine is None:
        with _ocr_lock:
            if ocr_engine is None:
                try:
                    ocr_engine = PaddleOCR(
                        use_angle_cls=True,
                        lang=lang,
                        use_gpu=use_gpu,
                        show_log=False
                    )
                except Exception as e:
                    logger.error(f"OCR 引擎初始化失败: {e}")
                    return None

    return ocr_engine


def is_ocr_available():
    """检查OCR功能是否可用"""
    return OCR_AVAILABLE


def ocr_image(image_path):
    """
    对图片进行OCR识别(带超时保护)

    Args:
        image_path: 图片路径

    Returns:
        list: 识别结果列表，每项为 (text, confidence, box)
        None: 如果OCR不可用或识别失败或超时
    """
    if not OCR_AVAILABLE:
        return None

    engine = init_ocr()
    if engine is None:
        return None

    def _do_ocr():
        return engine.ocr(image_path, cls=True)

    try:
        # 使用线程池+超时保护,避免 OCR 卡死阻塞 worker
        future = _ocr_executor.submit(_do_ocr)
        result = future.result(timeout=OCR_TIMEOUT)
        if result and len(result) > 0:
            texts = []
            for line in result[0]:
                if line:
                    box = line[0]
                    text_info = line[1]
                    texts.append({
                        'text': text_info[0],
                        'confidence': float(text_info[1]),
                        'box': box
                    })
            return texts
    except FuturesTimeoutError:
        logger.error(f"OCR 识别超时({OCR_TIMEOUT}s),图片: {image_path}")
    except Exception as e:
        logger.error(f"OCR识别失败: {e}", exc_info=True)

    return None


def extract_business_license(image_path):
    """
    从营业执照图片中提取关键信息
    
    Args:
        image_path: 营业执照图片路径
    
    Returns:
        dict: 提取的信息字典，包含：
            - credit_code: 统一社会信用代码
            - name: 企业名称
            - legal_person: 法定代表人
            - capital: 注册资本
            - establish_date: 成立日期
            - address: 企业地址
            - business_scope: 经营范围（可选）
            - valid_period: 营业期限（可选）
    """
    result = {
        'success': False,
        'message': '',
        'data': {}
    }
    
    if not OCR_AVAILABLE:
        result['message'] = 'AI识别功能未启用，请安装 paddleocr 库'
        return result
    
    ocr_result = ocr_image(image_path)
    if not ocr_result:
        result['message'] = '图片识别失败，请检查图片是否清晰'
        return result
    
    # 提取所有识别到的文本
    all_texts = [item['text'] for item in ocr_result]
    full_text = ' '.join(all_texts)
    
    data = {}
    
    # 统一社会信用代码（18位）
    credit_code_pattern = r'[0-9A-Z]{18}'
    for text in all_texts:
        match = re.search(credit_code_pattern, text)
        if match:
            data['credit_code'] = match.group()
            break
    
    # 企业名称
    name_patterns = [
        r'名称\s*[:：]?\s*(.+?)(?:\s|$)',
        r'企业名称\s*[:：]?\s*(.+?)(?:\s|$)',
    ]
    for pattern in name_patterns:
        match = re.search(pattern, full_text)
        if match:
            data['name'] = match.group(1).strip()
            break
    
    # 从识别结果中查找名称（通常在"名称"关键字附近）
    if 'name' not in data:
        for i, text in enumerate(all_texts):
            if '名称' in text and len(text) > 2:
                # 尝试从同一行或下一行提取
                if ':' in text or '：' in text:
                    parts = re.split(r'[:：]', text)
                    if len(parts) > 1:
                        data['name'] = parts[1].strip()
                        break
                elif i + 1 < len(all_texts):
                    data['name'] = all_texts[i + 1].strip()
                    break
    
    # 法定代表人
    legal_patterns = [
        r'法定代表人\s*[:：]?\s*(.+?)(?:\s|$)',
        r'负责人\s*[:：]?\s*(.+?)(?:\s|$)',
    ]
    for pattern in legal_patterns:
        match = re.search(pattern, full_text)
        if match:
            data['legal_person'] = match.group(1).strip()
            break
    
    # 注册资本
    capital_patterns = [
        r'注册资本\s*[:：]?\s*(.+?)(?:\s|$)',
        r'资金数额\s*[:：]?\s*(.+?)(?:\s|$)',
    ]
    for pattern in capital_patterns:
        match = re.search(pattern, full_text)
        if match:
            data['capital'] = match.group(1).strip()
            break
    
    # 成立日期
    date_patterns = [
        r'成立日期\s*[:：]?\s*(\d{4}年\d{1,2}月\d{1,2}日)',
        r'成立日期\s*[:：]?\s*(\d{4}-\d{2}-\d{2})',
        r'成立日期\s*[:：]?\s*(\d{4}\.\d{2}\.\d{2})',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, full_text)
        if match:
            date_str = match.group(1)
            # 统一转换为 YYYY-MM-DD 格式
            date_str = date_str.replace('年', '-').replace('月', '-').replace('日', '')
            date_str = date_str.replace('.', '-')
            data['establish_date'] = date_str
            break
    
    # 住所/地址
    address_patterns = [
        r'住所\s*[:：]?\s*(.+?)(?:\s*$)',
        r'地址\s*[:：]?\s*(.+?)(?:\s*$)',
        r'经营场所\s*[:：]?\s*(.+?)(?:\s|$)',
    ]
    for pattern in address_patterns:
        match = re.search(pattern, full_text)
        if match:
            data['address'] = match.group(1).strip()
            break
    
    # 如果没找到，尝试从文本末尾查找（地址通常在右下角）
    if 'address' not in data:
        for i in range(len(all_texts) - 1, -1, -1):
            text = all_texts[i]
            # 地址通常包含省市区等信息
            if any(province in text for province in ['省', '市', '区', '县', '路', '号']):
                if len(text) > 10:  # 地址通常较长
                    data['address'] = text.strip()
                    break
    
    result['success'] = True
    result['message'] = '识别成功'
    result['data'] = data
    result['raw_texts'] = all_texts  # 返回原始识别文本供参考
    
    return result


def extract_invoice(image_path):
    """
    从发票图片中提取关键信息
    
    Args:
        image_path: 发票图片路径
    
    Returns:
        dict: 提取的信息字典，包含：
            - invoice_code: 发票代码
            - invoice_number: 发票号码
            - invoice_date: 开票日期
            - buyer_name: 购买方名称
            - seller_name: 销售方名称
            - total_amount: 含税金额
            - tax_rate: 税率
            - amount_without_tax: 不含税金额
            - tax_amount: 税额
    """
    result = {
        'success': False,
        'message': '',
        'data': {}
    }
    
    if not OCR_AVAILABLE:
        result['message'] = 'AI识别功能未启用，请安装 paddleocr 库'
        return result
    
    ocr_result = ocr_image(image_path)
    if not ocr_result:
        result['message'] = '图片识别失败，请检查图片是否清晰'
        return result
    
    all_texts = [item['text'] for item in ocr_result]
    full_text = ' '.join(all_texts)
    
    data = {}
    
    # 发票代码（通常10-12位数字）
    code_patterns = [
        r'发票代码\s*[:：]?\s*(\d{10,12})',
        r'代码\s*[:：]?\s*(\d{10,12})',
    ]
    for pattern in code_patterns:
        match = re.search(pattern, full_text)
        if match:
            data['invoice_code'] = match.group(1)
            break
    
    # 发票号码（通常8位数字）
    number_patterns = [
        r'发票号码\s*[:：]?\s*(\d{8})',
        r'号码\s*[:：]?\s*(\d{8})',
        r'No\.?\s*(\d{8})',
    ]
    for pattern in number_patterns:
        match = re.search(pattern, full_text)
        if match:
            data['invoice_number'] = match.group(1)
            break
    
    # 开票日期
    date_patterns = [
        r'开票日期\s*[:：]?\s*(\d{4}年\d{1,2}月\d{1,2}日)',
        r'开票日期\s*[:：]?\s*(\d{4}-\d{2}-\d{2})',
        r'开票日期\s*[:：]?\s*(\d{4}\.\d{2}\.\d{2})',
        r'日期\s*[:：]?\s*(\d{4}年\d{1,2}月\d{1,2}日)',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, full_text)
        if match:
            date_str = match.group(1)
            date_str = date_str.replace('年', '-').replace('月', '-').replace('日', '')
            date_str = date_str.replace('.', '-')
            data['invoice_date'] = date_str
            break
    
    # 购买方信息
    buyer_patterns = [
        r'购买方[名称]?\s*[:：]?\s*(.+?)(?:\s|$)',
        r'购[买方]*[名称]?\s*[:：]?\s*(.+?)(?:\s|$)',
    ]
    for pattern in buyer_patterns:
        match = re.search(pattern, full_text)
        if match:
            data['buyer_name'] = match.group(1).strip()
            break
    
    # 销售方信息
    seller_patterns = [
        r'销售方[名称]?\s*[:：]?\s*(.+?)(?:\s|$)',
        r'销[售方]*[名称]?\s*[:：]?\s*(.+?)(?:\s|$)',
    ]
    for pattern in seller_patterns:
        match = re.search(pattern, full_text)
        if match:
            data['seller_name'] = match.group(1).strip()
            break
    
    # 金额提取
    # 合计金额（含税）
    total_patterns = [
        r'[合总]计[金额]?\s*[:：]?\s*￥?\s*([\d,]+\.?\d*)',
        r'[合总]计\s*[:：]?\s*￥?\s*([\d,]+\.?\d*)',
        r'价税合计[（(]大写[)）].*?[（(]小写[)）]\s*[:：]?\s*￥?\s*([\d,]+\.?\d*)',
    ]
    for pattern in total_patterns:
        match = re.search(pattern, full_text)
        if match:
            amount_str = match.group(1).replace(',', '')
            try:
                data['total_amount'] = float(amount_str)
            except ValueError:
                pass
            break
    
    # 不含税金额
    amount_patterns = [
        r'金额\s*[:：]?\s*￥?\s*([\d,]+\.?\d*)',
        r'不含税金额\s*[:：]?\s*￥?\s*([\d,]+\.?\d*)',
    ]
    for pattern in amount_patterns:
        match = re.search(pattern, full_text)
        if match:
            amount_str = match.group(1).replace(',', '')
            try:
                data['amount_without_tax'] = float(amount_str)
            except ValueError:
                pass
            break
    
    # 税额
    tax_patterns = [
        r'税额\s*[:：]?\s*￥?\s*([\d,]+\.?\d*)',
        r'税[款额]\s*[:：]?\s*￥?\s*([\d,]+\.?\d*)',
    ]
    for pattern in tax_patterns:
        match = re.search(pattern, full_text)
        if match:
            tax_str = match.group(1).replace(',', '')
            try:
                data['tax_amount'] = float(tax_str)
            except ValueError:
                pass
            break
    
    # 税率（如13%、9%、6%等）
    rate_patterns = [
        r'税率\s*[:：]?\s*(\d+)%',
        r'(\d+)%税率',
    ]
    for pattern in rate_patterns:
        match = re.search(pattern, full_text)
        if match:
            data['tax_rate'] = float(match.group(1))
            break
    
    result['success'] = True
    result['message'] = '识别成功'
    result['data'] = data
    result['raw_texts'] = all_texts
    
    return result