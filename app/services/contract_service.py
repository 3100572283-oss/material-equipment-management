"""合同生成服务

提供合同模板变量替换渲染、Word文档生成、合同编号生成等功能。

甲方固定信息：
- 名称：中铁二十一局集团第六工程有限公司
- 地址：天津滨海高新区塘沽海洋科技园盛安建设发展大厦1-2，4，5-106
- 邮编：300459
- 纳税人识别号：91110302584495229A
- 开户银行：中国银行股份有限公司天津渤龙湖支行
- 账号：268794756854
"""
import os
import re
import json
from datetime import datetime, date
from decimal import Decimal


# ============================================================
# 甲方固定信息常量
# ============================================================

PARTY_A_INFO = {
    'name': '中铁二十一局集团第六工程有限公司',
    'address': '天津滨海高新区塘沽海洋科技园盛安建设发展大厦1-2，4，5-106',
    'zipcode': '300459',
    'taxpayer_type': '增值税一般纳税人',
    'taxpayer_id': '91110302584495229A',
    'bank_name': '中国银行股份有限公司天津渤龙湖支行',
    'bank_account': '268794756854',
}


# ============================================================
# 变量替换渲染
# ============================================================

def render_template(template_content, variables):
    """将模板内容中的 {{变量名}} 占位符替换为实际值

    Args:
        template_content: 模板正文（含 {{变量名}} 占位符）
        variables: dict 变量值映射

    Returns:
        str: 渲染后的合同文本
    """
    if not template_content:
        return ''

    if not variables:
        variables = {}

    rendered = template_content

    # 替换 {{ 变量名 }} 格式的占位符（支持空格）
    def replace_var(match):
        var_name = match.group(1).strip()
        value = variables.get(var_name, '')
        if value is None:
            value = ''
        return str(value)

    rendered = re.sub(r'\{\{\s*(\w+)\s*\}\}', replace_var, rendered)

    # 兼容 [[变量名]] 格式
    def replace_bracket_var(match):
        var_name = match.group(1).strip()
        value = variables.get(var_name, '')
        if value is None:
            value = ''
        return str(value)

    rendered = re.sub(r'\[\[(\w+)\]\]', replace_bracket_var, rendered)

    return rendered


def render_template_html(template_content, variables):
    """将模板内容渲染为HTML格式（用于预览）

    将Markdown表格转换为HTML表格，并替换变量占位符。

    Args:
        template_content: 模板正文（Markdown格式，含 {{变量名}} 占位符）
        variables: dict 变量值映射

    Returns:
        str: 渲染后的HTML文本
    """
    # 先替换变量
    rendered = render_template(template_content, variables)

    # 简单的Markdown到HTML转换
    lines = rendered.split('\n')
    html_parts = []
    in_table = False
    table_rows = []

    for line in lines:
        stripped = line.strip()

        # 检测表格行
        if stripped.startswith('|') and stripped.endswith('|'):
            if not in_table:
                in_table = True
                table_rows = []

            cells = [c.strip() for c in stripped.split('|')[1:-1]]

            # 跳过分隔行（如 |---|---|）
            if all(re.match(r'^[-:]+$', c) for c in cells):
                continue

            table_rows.append(cells)
            continue
        else:
            if in_table:
                # 输出表格
                if table_rows:
                    html_parts.append('<table border="1" style="border-collapse:collapse;width:100%;">')
                    for i, row in enumerate(table_rows):
                        html_parts.append('<tr>')
                        tag = 'th' if i == 0 else 'td'
                        for cell in row:
                            html_parts.append('<{} style="border:1px solid #999;padding:4px;">{}</{}>'.format(tag, cell, tag))
                        html_parts.append('</tr>')
                    html_parts.append('</table>')
                in_table = False
                table_rows = []

        # 标题
        if stripped.startswith('# '):
            html_parts.append('<h1>{}</h1>'.format(stripped[2:]))
        elif stripped.startswith('## '):
            html_parts.append('<h2>{}</h2>'.format(stripped[3:]))
        elif stripped.startswith('### '):
            html_parts.append('<h3>{}</h3>'.format(stripped[4:]))
        elif stripped:
            html_parts.append('<p>{}</p>'.format(stripped))
        else:
            html_parts.append('<br/>')

    # 处理末尾表格
    if in_table and table_rows:
        html_parts.append('<table border="1" style="border-collapse:collapse;width:100%;">')
        for i, row in enumerate(table_rows):
            html_parts.append('<tr>')
            tag = 'th' if i == 0 else 'td'
            for cell in row:
                html_parts.append('<{} style="border:1px solid #999;padding:4px;">{}</{}>'.format(tag, cell, tag))
            html_parts.append('</tr>')
        html_parts.append('</table>')

    return '\n'.join(html_parts)


def render_table_data(variables_schema, variable_values):
    """渲染表格型变量数据

    将表格型变量（如物资清单）渲染为Markdown表格行。

    Args:
        variables_schema: list 变量schema列表
        variable_values: dict 变量值

    Returns:
        dict: 表格变量名到渲染后文本的映射
    """
    result = {}
    if not variables_schema:
        return result

    for var in variables_schema:
        if var.get('type') == 'table':
            var_name = var['name']
            columns = var.get('columns', [])
            rows = variable_values.get(var_name, [])

            if not rows:
                result[var_name] = ''
                continue

            # 生成Markdown表格
            lines = []
            # 表头
            header = '|'.join(col.get('label', col['name']) for col in columns)
            separator = '|'.join('---' for _ in columns)
            lines.append('|{}|'.format(header))
            lines.append('|{}|'.format(separator))

            # 数据行
            for row in rows:
                cells = []
                for col in columns:
                    val = row.get(col['name'], '')
                    if val is None:
                        val = ''
                    cells.append(str(val))
                lines.append('|' + '|'.join(cells) + '|')

            result[var_name] = '\n'.join(lines)

    return result


# ============================================================
# Word文档生成
# ============================================================

def generate_word_document(template_content, variables, output_path, template_name=''):
    """使用 python-docx 生成Word文档

    Word文档要求：
    - 标题居中、加粗
    - 正文使用宋体小四
    - 表格自动生成（物资清单等）
    - 甲方信息固定

    Args:
        template_content: 模板正文（Markdown格式，含 {{变量名}} 占位符）
        variables: dict 变量值映射
        output_path: str 输出文件路径
        template_name: str 合同类型名称

    Returns:
        str: 生成的文件路径
    """
    from docx import Document
    from docx.shared import Pt, Cm, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 先渲染模板文本
    rendered = render_template(template_content, variables)

    # 创建Word文档
    doc = Document()

    # 设置默认字体（宋体小四 = 12pt）
    style = doc.styles['Normal']
    font = style.font
    font.name = '宋体'
    font.size = Pt(12)
    style.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')

    # 设置页边距
    for section in doc.sections:
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(3.18)
        section.right_margin = Cm(3.18)

    # 解析渲染后的文本，逐行写入
    lines = rendered.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 跳过空行
        if not line:
            i += 1
            continue

        # 检测标题（# 开头）
        if line.startswith('# ') and not line.startswith('## '):
            title_text = line[2:].strip()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(title_text)
            run.bold = True
            run.font.size = Pt(16)
            run.font.name = '宋体'
            run.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
            i += 1
            continue

        if line.startswith('## '):
            heading_text = line[3:].strip()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(heading_text)
            run.bold = True
            run.font.size = Pt(14)
            run.font.name = '宋体'
            run.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
            i += 1
            continue

        if line.startswith('### '):
            heading_text = line[4:].strip()
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(heading_text)
            run.bold = True
            run.font.size = Pt(12)
            run.font.name = '宋体'
            run.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
            i += 1
            continue

        # 检测表格（| 开头且以 | 结尾）
        if line.startswith('|') and line.endswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|') and lines[i].strip().endswith('|'):
                table_lines.append(lines[i].strip())
                i += 1

            # 解析表格
            table_data = []
            for tl in table_lines:
                cells = [c.strip() for c in tl.split('|')[1:-1]]
                # 跳过分隔行
                if all(re.match(r'^[-:]+$', c) for c in cells):
                    continue
                table_data.append(cells)

            if table_data:
                # 创建表格
                num_cols = max(len(row) for row in table_data)
                table = doc.add_table(rows=len(table_data), cols=num_cols)
                table.style = 'Table Grid'
                table.alignment = WD_TABLE_ALIGNMENT.CENTER

                for row_idx, row_data in enumerate(table_data):
                    for col_idx, cell_text in enumerate(row_data):
                        if col_idx < num_cols:
                            cell = table.cell(row_idx, col_idx)
                            cell.text = cell_text
                            # 设置单元格字体
                            for paragraph in cell.paragraphs:
                                for run in paragraph.runs:
                                    run.font.name = '宋体'
                                    run.font.size = Pt(10)
                                    run.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
                                # 表头加粗
                                if row_idx == 0:
                                    for run in paragraph.runs:
                                        run.bold = True
            continue

        # 普通段落
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        # 段落首行缩进2字符
        p.paragraph_format.first_line_indent = Cm(0.74)
        # 段落行间距
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.line_spacing = 1.5

        run = p.add_run(line)
        run.font.name = '宋体'
        run.font.size = Pt(12)
        run.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')

        i += 1

    # 保存文档
    doc.save(output_path)
    return output_path


# ============================================================
# 合同编号生成
# ============================================================

def generate_contract_no(project_id, template_code):
    """生成合同编号

    格式：模板前缀-项目ID-年月日-序号
    如：WSMM-1-20250101-001

    Args:
        project_id: int 项目ID
        template_code: str 模板代码

    Returns:
        str: 合同编号
    """
    # 模板代码到前缀的映射
    CODE_PREFIX_MAP = {
        'material_purchase': 'WSMM',
        'turnover_material_lease': 'ZZCL',
        'equipment_lease': 'JXSB',
        'construction_equipment_purchase': 'JSCG',
        'general_goods_sale': 'MMHT',
        'transport_entrust': 'WTYS',
        'material_supplement': 'CLBC',
        'waste_disposal': 'FJCZ',
    }

    prefix = CODE_PREFIX_MAP.get(template_code, 'HT')
    date_str = datetime.now().strftime('%Y%m%d')

    # 尝试查询数据库获取序号
    try:
        from app.models import ContractInstance
        from app import db
        count = ContractInstance.query.filter(
            ContractInstance.contract_no.like('{}-{}-{}-%'.format(prefix, project_id, date_str))
        ).count()
        seq = '{:03d}'.format(count + 1)
    except Exception:
        seq = datetime.now().strftime('%H%M%S')

    return '{}-{}-{}-{}'.format(prefix, project_id, date_str, seq)


# ============================================================
# 金额大写转换
# ============================================================

def amount_to_chinese(amount):
    """将数字金额转换为中文大写

    Args:
        amount: float 数字金额

    Returns:
        str: 中文大写金额
    """
    if amount is None or amount == '':
        return ''

    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return ''

    if amount == 0:
        return '零元整'

    # 数字到中文映射
    digit_map = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖']
    unit_map = ['', '拾', '佰', '仟']
    big_unit_map = ['', '万', '亿', '兆']

    # 分离整数和小数部分
    integer_part = int(amount)
    decimal_part = round((amount - integer_part) * 100)

    # 处理整数部分
    if integer_part == 0:
        integer_str = ''
    else:
        integer_str = ''
        str_int = str(integer_part)
        length = len(str_int)
        zero_flag = False

        for idx, digit in enumerate(str_int):
            pos = length - 1 - idx
            big_pos = pos // 4
            small_pos = pos % 4
            d = int(digit)

            if d == 0:
                zero_flag = True
            else:
                if zero_flag:
                    integer_str += '零'
                    zero_flag = False
                integer_str += digit_map[d] + unit_map[small_pos]

            # 添加大单位
            if small_pos == 0 and big_pos > 0:
                if not zero_flag or big_pos > 0:
                    integer_str += big_unit_map[big_pos]
                zero_flag = False

        integer_str += '元'

    # 处理小数部分
    decimal_str = ''
    if decimal_part > 0:
        jiao = decimal_part // 10
        fen = decimal_part % 10
        if jiao > 0:
            decimal_str += digit_map[jiao] + '角'
        if fen > 0:
            decimal_str += digit_map[fen] + '分'
    else:
        decimal_str = '整'

    return integer_str + decimal_str


# ============================================================
# 变量校验
# ============================================================

def validate_variables(variables_schema, variable_values):
    """校验必填变量是否已填写

    Args:
        variables_schema: list 变量schema列表
        variable_values: dict 用户填写的变量值

    Returns:
        tuple: (is_valid: bool, missing_fields: list)
    """
    missing = []
    for var in variables_schema:
        if var.get('required') and var.get('type') != 'table':
            var_name = var['name']
            value = variable_values.get(var_name)
            if value is None or value == '' or value == []:
                missing.append(var.get('label', var_name))

    return len(missing) == 0, missing


# ============================================================
# 合同实例生成
# ============================================================

def generate_contract_instance(template, variable_values, project_id=None, created_by=None):
    """生成合同实例

    Args:
        template: ContractTemplate 模板对象
        variable_values: dict 变量值
        project_id: int 项目ID
        created_by: int 创建者ID

    Returns:
        ContractInstance: 合同实例对象
    """
    from app.models import ContractInstance
    from app import db

    # 生成合同编号
    contract_no = variable_values.get('contract_no') or generate_contract_no(project_id, template.code)

    # 生成标题
    title = '{}-{}'.format(template.name, contract_no)

    # 创建实例
    instance = ContractInstance(
        template_id=template.id,
        project_id=project_id,
        contract_no=contract_no,
        title=title,
        status='draft',
        created_by=created_by,
    )
    instance.set_variable_values(variable_values)

    db.session.add(instance)
    db.session.commit()

    return instance
