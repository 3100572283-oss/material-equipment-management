#!/usr/bin/env python3
"""
P2: 基础监控告警脚本
监控系统资源、服务状态、错误率，写入日志并发送告警
部署为cron job: */5 * * * * /opt/material-equipment-management/venv/bin/python3 /opt/material-equipment-management/monitor.py
"""
import os
import sys
import time
import psutil
import subprocess
import json
from datetime import datetime, timedelta

sys.path.insert(0, '/opt/material-equipment-management')
os.environ.setdefault('ENCRYPTION_KEY', [l.split('=',1)[1].strip() for l in open('/opt/material-equipment-management/.env') if l.startswith('ENCRYPTION_KEY')][0])

LOG_FILE = '/var/log/material-monitor.log'
ALERT_THRESHOLD_CPU = 90      # CPU使用率阈值
ALERT_THRESHOLD_MEM = 85      # 内存使用率阈值
ALERT_THRESHOLD_DISK = 80     # 磁盘使用率阈值
ALERT_THRESHOLD_ERROR_RATE = 10  # 5分钟内错误数阈值
SERVICES = ['material-old.service', 'nginx', 'mysql']

def log(msg, level='INFO'):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [{level}] {msg}'
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')
    if level == 'ALERT':
        print(line, file=sys.stderr)
    else:
        print(line)

def check_system_resources():
    alerts = []
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')

    if cpu > ALERT_THRESHOLD_CPU:
        alerts.append(f'CPU使用率过高: {cpu:.1f}%')
    if mem.percent > ALERT_THRESHOLD_MEM:
        alerts.append(f'内存使用率过高: {mem.percent:.1f}%')
    if disk.percent > ALERT_THRESHOLD_DISK:
        alerts.append(f'磁盘使用率过高: {disk.percent:.1f}%')

    log(f'CPU:{cpu:.1f}% MEM:{mem.percent:.1f}% DISK:{disk.percent:.1f}%')
    return alerts

def check_services():
    alerts = []
    for svc in SERVICES:
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', svc],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            if status != 'active':
                alerts.append(f'服务异常: {svc} -> {status}')
                log(f'服务 {svc}: {status}', 'ALERT')
            else:
                log(f'服务 {svc}: active')
        except Exception as e:
            alerts.append(f'服务检查失败: {svc} -> {e}')
    return alerts

def check_error_rate():
    alerts = []
    try:
        from app import create_app
        from app.models import ErrorLog, db
        app = create_app()
        with app.app_context():
            cutoff = datetime.now() - timedelta(minutes=5)
            count = ErrorLog.query.filter(ErrorLog.error_time >= cutoff).count()
            if count > ALERT_THRESHOLD_ERROR_RATE:
                alerts.append(f'错误率告警: 5分钟内 {count} 条错误')
            log(f'5分钟内错误数: {count}')
    except Exception as e:
        log(f'错误率检查失败: {e}', 'ALERT')
    return alerts

def check_http_health():
    alerts = []
    try:
        result = subprocess.run(
            ['curl', '-sk', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '5', 'https://127.0.0.1/auth/login'],
            capture_output=True, text=True, timeout=10
        )
        code = result.stdout.strip()
        if code != '200':
            alerts.append(f'HTTP健康检查失败: /auth/login 返回 {code}')
            log(f'HTTP /auth/login: {code}', 'ALERT')
        else:
            log(f'HTTP /auth/login: 200 OK')
    except Exception as e:
        alerts.append(f'HTTP健康检查异常: {e}')
    return alerts

if __name__ == '__main__':
    log('=== 监控检查开始 ===')
    all_alerts = []

    all_alerts.extend(check_system_resources())
    all_alerts.extend(check_services())
    all_alerts.extend(check_error_rate())
    all_alerts.extend(check_http_health())

    if all_alerts:
        for alert in all_alerts:
            log(alert, 'ALERT')
        log(f'共 {len(all_alerts)} 条告警', 'ALERT')
    else:
        log('所有检查通过，无告警')

    log('=== 监控检查结束 ===\n')
