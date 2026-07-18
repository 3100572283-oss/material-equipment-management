import os
from app import create_app, db
from app.models import User, Project, Supplier, Category, Material, UsageUnit, WorkNumber

app = create_app()

# 初始化定时任务调度器
try:
    from app.scheduler import init_scheduler
    init_scheduler(app)
except Exception as e:
    print(f"Scheduler init failed (non-critical): {e}")


@app.shell_context_processor
def make_shell_context():
    return {
        'db': db,
        'User': User,
        'Project': Project,
        'Supplier': Supplier,
        'Category': Category,
        'Material': Material,
        'UsageUnit': UsageUnit,
        'WorkNumber': WorkNumber
    }


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
