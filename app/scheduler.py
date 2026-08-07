import os
"""定时任务调度"""
from datetime import datetime


def init_scheduler(app):
    """初始化APScheduler定时任务"""
    # 文件锁防止gunicorn多worker重复启动调度器
    import fcntl
    lock_file = '/opt/material-equipment-management/scheduler.lock'
    lock_fp = open(lock_file, 'w')
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("APScheduler: 另一个worker已启动调度器，跳过")
        return None
    lock_fp.write(str(os.getpid()))
    lock_fp.flush()
    # 将lock_fp存储到app对象上，防止GC回收导致锁释放
    app._scheduler_lock = lock_fp

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("APScheduler not installed, skipping scheduler init")
        return None

    scheduler = BackgroundScheduler()

    def daily_warning_job():
        with app.app_context():
            from app.notification_service import notify_daily_warnings
            try:
                notify_daily_warnings()
                print(f"[Scheduler] Daily warnings pushed at {datetime.now()}")
            except Exception as e:
                print(f"[Scheduler] Daily warning error: {e}")

    # 每天早上9点推送预警
    scheduler.add_job(
        daily_warning_job,
        CronTrigger(hour=9, minute=0),
        id='daily_warnings',
        replace_existing=True
    )

    def inventory_warning_job():
        with app.app_context():
            from app.notification_service import notify_inventory_warning
            try:
                count = notify_inventory_warning()
                if count > 0:
                    print(f"[Scheduler] Inventory warnings: {count} items at {datetime.now()}")
            except Exception as e:
                print(f"[Scheduler] Inventory warning error: {e}")

    # 每天上午10点检查库存预警
    scheduler.add_job(
        inventory_warning_job,
        CronTrigger(hour=10, minute=0),
        id='inventory_warnings',
        replace_existing=True
    )

    def cleanup_audit_logs():
        with app.app_context():
            from app import db
            from app.models import SysOperationLog
            from app.utils import get_config
            from datetime import timedelta
            retention = int(get_config('audit_log_retention_days', '365'))
            cutoff = datetime.now() - timedelta(days=retention)
            SysOperationLog.query.filter(SysOperationLog.operation_time < cutoff).delete()
            db.session.commit()
            print(f"[Scheduler] Audit logs older than {retention} days cleaned at {datetime.now()}")

    # 每天凌晨2点清理过期日志
    scheduler.add_job(
        cleanup_audit_logs,
        CronTrigger(hour=2, minute=0),
        id='cleanup_audit_logs',
        replace_existing=True
    )

    def cleanup_recycle_bin():
        with app.app_context():
            from app import db
            from app.utils import get_config
            from datetime import timedelta
            from sqlalchemy import text
            retention = int(get_config('recycle_bin_retention_days', '30'))
            cutoff = datetime.now() - timedelta(days=retention)
            tables = ['contracts', 'stock_ins', 'stock_outs', 'suppliers', 'materials',
                      'payments', 'reconciliations', 'equipment', 'turnover_material']
            for tbl in tables:
                db.session.execute(text(
                    f"DELETE FROM {tbl} WHERE is_deleted = 1 AND deleted_at < :cutoff"
                ), {'cutoff': cutoff})
            db.session.commit()
            print(f"[Scheduler] Recycle bin cleaned at {datetime.now()}")

    # 每天凌晨3点清理过期回收站
    scheduler.add_job(
        cleanup_recycle_bin,
        CronTrigger(hour=3, minute=0),
        id='cleanup_recycle_bin',
        replace_existing=True
    )

    def cost_profit_job():
        with app.app_context():
            from app.cost.services import refresh_profit_analysis, push_profit_warnings
            try:
                n = refresh_profit_analysis()
                sent = push_profit_warnings()
                print(f"[Scheduler] Cost profit analysis refreshed: {n} rows, warnings pushed: {sent} at {datetime.now()}")
            except Exception as e:
                print(f"[Scheduler] Cost profit error: {e}")

    # 每月1日 02:30 刷新盈亏分析并推送预警
    scheduler.add_job(
        cost_profit_job,
        CronTrigger(day=1, hour=2, minute=30),
        id='cost_profit',
        replace_existing=True
    )

    def subcontractor_expiry_job():
        """M6：分包商准入有效期即将到期预警，推送站内消息给准入审批人。"""
        with app.app_context():
            from datetime import timedelta
            from app.subcontractor.models import SubcontractorProfile
            from app.auth_core.models import (AuthPermission, AuthRolePermission,
                                               AuthUserRole)
            from app.notification_service import send_message, MSG_TYPE_WARNING
            try:
                horizon = datetime.now().date() + timedelta(days=30)
                today = datetime.now().date()
                profiles = (SubcontractorProfile.query
                            .filter(SubcontractorProfile.admit_status == 'approved',
                                    SubcontractorProfile.valid_to.isnot(None),
                                    SubcontractorProfile.valid_to <= horizon,
                                    SubcontractorProfile.valid_to >= today)
                            .all())
                if not profiles:
                    print("[Scheduler] subcontractor expiry: none due")
                    return
                perm = AuthPermission.query.filter_by(
                    perm_key='subcontractor:profile:review').first()
                recipients = set()
                if perm:
                    role_ids = [rp.role_id for rp in
                                AuthRolePermission.query.filter_by(permission_id=perm.id)]
                    for ur in AuthUserRole.query.filter(
                            AuthUserRole.role_id.in_(role_ids)).all():
                        recipients.add(ur.user_id)
                sent = 0
                for p in profiles:
                    name = p.supplier.name if p.supplier else ('#%d' % p.id)
                    for uid in recipients:
                        send_message(uid, MSG_TYPE_WARNING, '分包商准入即将到期',
                                     '分包商「%s」准入有效期至 %s，请及时办理续期或重新核验。'
                                     % (name, p.valid_to))
                        sent += 1
                print("[Scheduler] subcontractor expiry warnings sent: %d" % sent)
            except Exception as e:
                print("[Scheduler] subcontractor expiry error: %s" % e)

    # 每月1日 02:45 分包商准入到期预警
    scheduler.add_job(
        subcontractor_expiry_job,
        CronTrigger(day=1, hour=2, minute=45),
        id='subcontractor_expiry',
        replace_existing=True
    )

    scheduler.start()
    print("APScheduler started: daily_warnings@09:00, inventory_warnings@10:00, cleanup_audit@02:00, cleanup_recycle@03:00, cost_profit@monthly(01 02:30), subcontractor_expiry@monthly(01 02:45)")
    return scheduler
