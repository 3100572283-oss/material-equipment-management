"""定时任务调度"""
from datetime import datetime


def init_scheduler(app):
    """初始化APScheduler定时任务"""
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

    scheduler.start()
    print("APScheduler started: daily_warnings@09:00, cleanup_audit@02:00, cleanup_recycle@03:00")
    return scheduler
