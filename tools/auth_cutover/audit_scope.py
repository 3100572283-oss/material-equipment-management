# -*- coding: utf-8 -*-
"""Phase2 数据范围审计：扫描全部蓝图路由，标出「查了带 project_id 的模型但没做范围过滤」的函数。

判定逻辑（AST，非正则猜）：
  1. 解析 app/<bp>/**/*.py，取所有被 @xxx.route 装饰的函数
  2. 函数体内出现 `<Model>.query` 且该 Model 在 app.models 里有 project_id 列 -> 需要范围过滤
  3. 函数体内出现任一强制标记 -> 视为已过滤
     apply_data_scope_filter / get_user_allowed_projects / get_accessible_projects /
     get_user_visible_projects / can_access_project / current_project_id / allowed_projects
  4. 写操作（POST-only 路由）与详情页单独归类
输出：CSV + 汇总
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..', '..')))

import ast
import io
import csv

from run import app
from app import db

ENFORCE_TOKENS = (
    'apply_data_scope_filter',
    'get_user_allowed_projects',
    'get_accessible_projects',
    'get_user_visible_projects',
    'can_access_project',
    'get_allowed_projects',
    'current_project_id',
    'allowed_projects',
    'scope_filter',
)

SKIP_DIRS = {'__pycache__', 'static', 'templates', 'services', 'auth_core', 'auth', 'ai',
             'ocr_service', 'help', 'tools', 'main', 'profile', 'message'}


def project_scoped_models():
    """从 SQLAlchemy 元数据取出带 project_id 列的模型类名集合"""
    names = set()
    tables = set()
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        try:
            cols = {c.name for c in mapper.local_table.columns}
        except Exception:
            continue
        if 'project_id' in cols:
            names.add(cls.__name__)
            tables.add(mapper.local_table.name)
    return names, tables


class RouteVisitor(ast.NodeVisitor):
    def __init__(self, src):
        self.src = src
        self.routes = []

    def _is_route(self, node):
        methods = None
        hit = False
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'route':
                hit = True
                for kw in d.keywords or []:
                    if kw.arg == 'methods':
                        try:
                            methods = [ast.literal_eval(kw.value)]
                            methods = methods[0]
                        except Exception:
                            methods = None
        return hit, methods

    def visit_FunctionDef(self, node):
        hit, methods = self._is_route(node)
        if hit:
            seg = ast.get_source_segment(self.src, node) or ''
            self.routes.append((node.name, node.lineno, methods or ['GET'], seg))
        self.generic_visit(node)


def main():
    with app.app_context():
        model_names, _ = project_scoped_models()

    rows = []
    root = 'app'
    for bp in sorted(_os.listdir(root)):
        d = _os.path.join(root, bp)
        if not _os.path.isdir(d) or bp in SKIP_DIRS:
            continue
        for dirpath, dirnames, filenames in _os.walk(d):
            dirnames[:] = [x for x in dirnames if x != '__pycache__']
            for fn in filenames:
                if not fn.endswith('.py'):
                    continue
                path = _os.path.join(dirpath, fn)
                try:
                    src = io.open(path, encoding='utf-8').read()
                    tree = ast.parse(src)
                except Exception as e:
                    rows.append([bp, path, '<PARSE_ERROR>', '', '', str(e)[:60]])
                    continue
                v = RouteVisitor(src)
                v.visit(tree)
                for name, lineno, methods, seg in v.routes:
                    used = sorted({m for m in model_names if (m + '.query') in seg})
                    if not used:
                        continue
                    enforced = [t for t in ENFORCE_TOKENS if t in seg]
                    is_write = set(m.upper() for m in methods) <= {'POST', 'PUT', 'DELETE', 'PATCH'}
                    rows.append([
                        bp, path.replace('app/', ''), '%s:%d' % (name, lineno),
                        ','.join(methods),
                        'WRITE' if is_write else 'READ',
                        'OK' if enforced else 'MISSING',
                        ';'.join(used[:6]),
                        ';'.join(enforced[:3]),
                    ])

    rows.sort(key=lambda r: (r[5] != 'MISSING', r[0], r[2]))
    out = 'tools/auth_cutover/scope_audit.csv'
    with io.open(out, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['blueprint', 'file', 'func:line', 'methods', 'kind', 'status', 'models', 'enforced_by'])
        w.writerows(rows)

    miss = [r for r in rows if r[5] == 'MISSING']
    miss_read = [r for r in miss if r[4] == 'READ']
    ok = [r for r in rows if r[5] == 'OK']
    print('=' * 74)
    print('带 project_id 的模型数: %d' % len(model_names))
    print('命中路由总数: %d  |  已强制: %d  |  未强制: %d（其中读路由 %d）'
          % (len(rows), len(ok), len(miss), len(miss_read)))
    print('=' * 74)
    from collections import Counter
    c = Counter(r[0] for r in miss_read)
    print('未强制读路由 · 按蓝图排序（Top 25）:')
    for bpn, n in c.most_common(25):
        print('  %-24s %3d' % (bpn, n))
    print('=' * 74)
    print('明细已写入: %s' % out)


if __name__ == '__main__':
    main()
