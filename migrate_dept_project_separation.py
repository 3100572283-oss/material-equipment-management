"""
双树分离 - 历史数据迁移脚本
功能：
1. 将 sys_dept 表中 dept_type='project' 的项目部记录迁移到 projects 表
2. 建立项目的行政归属 dept_id 关联
3. 删除部门表中的项目部节点
4. 校准业务单据和用户的关联
5. 可重复执行、事务保证、失败可回滚
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db
from app.models import SysDept, Project, User, SysUserProject


def get_dept_path(dept, dept_map):
    """获取部门的完整路径"""
    path = []
    current = dept
    while current:
        path.insert(0, current.dept_name)
        if current.parent_id and current.parent_id in dept_map:
            current = dept_map[current.parent_id]
        else:
            current = None
    return ' / '.join(path)


def find_admin_dept_id(project_dept, dept_map):
    """
    查找项目部的行政归属部门（向上追溯，找到第一个非项目部类型的部门）
    """
    current = project_dept
    while current and current.parent_id:
        parent = dept_map.get(current.parent_id)
        if not parent:
            break
        if parent.dept_type != 'project':
            return parent.id
        current = parent
    return None


def migrate():
    app = create_app()
    with app.app_context():
        print("=" * 60)
        print(" 双树分离数据迁移开始")
        print("=" * 60)

        try:
            all_depts = SysDept.query.all()
            dept_map = {d.id: d for d in all_depts}

            project_depts = [d for d in all_depts if d.dept_type == 'project']
            print(f"\n[1/6] 发现 {len(project_depts)} 个项目部类型的部门记录")

            if not project_depts:
                print("  没有需要迁移的项目部记录，跳过数据迁移。")
                print("\n[验证] 当前 sys_dept 表部门类型统计：")
                type_counts = {}
                for d in SysDept.query.all():
                    t = d.dept_type
                    type_counts[t] = type_counts.get(t, 0) + 1
                for t, c in sorted(type_counts.items()):
                    print(f"  {t}: {c}")
                print("\n迁移完成（无数据需要迁移）。")
                return True

            for pd in project_depts:
                print(f"  - {pd.dept_code} / {pd.dept_name} "
                      f"(原项目ID: {pd.project_id}, 父部门ID: {pd.parent_id})")

            print(f"\n[2/6] 迁移项目部数据到 projects 表...")

            migrated_count = 0
            for pd in project_depts:
                admin_dept_id = find_admin_dept_id(pd, dept_map)
                admin_dept_name = dept_map[admin_dept_id].dept_name if admin_dept_id and admin_dept_id in dept_map else "无"

                existing_project = None
                if pd.project_id:
                    existing_project = Project.query.get(pd.project_id)

                if existing_project:
                    project = existing_project
                    project.name = pd.dept_name
                    if not project.code:
                        from app.utils import gen_project_code
                        project.code = gen_project_code()
                    if not project.dept_id:
                        project.dept_id = admin_dept_id
                    if not project.manager and pd.leader:
                        project.manager = pd.leader
                    if not project.remark and pd.remark:
                        project.remark = pd.remark
                    print(f"  更新已有项目: {project.name} (ID:{project.id}) → 归属部门: {admin_dept_name}")
                else:
                    from app.utils import gen_project_code
                    project = Project(
                        name=pd.dept_name,
                        code=gen_project_code(),
                        dept_id=admin_dept_id,
                        manager=pd.leader or None,
                        status='active',
                        remark=pd.remark or None,
                    )
                    db.session.add(project)
                    db.session.flush()
                    pd.project_id = project.id
                    print(f"  新建项目: {project.name} (ID:{project.id}) → 归属部门: {admin_dept_name}")

                migrated_count += 1

            print(f"  完成迁移 {migrated_count} 个项目")

            print(f"\n[3/6] 校准用户部门归属...")

            users_in_project_dept = User.query.filter(
                User.dept_id.in_([d.id for d in project_depts])
            ).all()

            print(f"  发现 {len(users_in_project_dept)} 个用户的部门是项目部")
            for user in users_in_project_dept:
                old_dept = dept_map.get(user.dept_id)
                admin_dept_id = find_admin_dept_id(old_dept, dept_map) if old_dept else None
                if admin_dept_id:
                    user.dept_id = admin_dept_id
                    admin_dept = dept_map.get(admin_dept_id)
                    print(f"    用户 {user.username}: 部门从 '{old_dept.dept_name}' → '{admin_dept.dept_name}'")

                    project_id = old_dept.project_id if old_dept else None
                    if project_id:
                        existing_link = SysUserProject.query.filter_by(
                            user_id=user.id, project_id=project_id
                        ).first()
                        if not existing_link:
                            main_exist = SysUserProject.query.filter_by(
                                user_id=user.id, is_main=True
                            ).first()
                            link = SysUserProject(
                                user_id=user.id,
                                project_id=project_id,
                                is_main=(main_exist is None)
                            )
                            db.session.add(link)
                            print(f"      → 新增项目关联: 项目ID {project_id}")

            print(f"\n[4/6] 删除部门表中的项目部节点...")

            for pd in project_depts:
                dept_children = [d for d in all_depts
                                 if d.parent_id == pd.id and d.dept_type != 'project']
                if dept_children:
                    admin_dept_id = find_admin_dept_id(pd, dept_map)
                    for child in dept_children:
                        child.parent_id = admin_dept_id or 0
                        print(f"  子部门 {child.dept_name} 上级改为: "
                              f"{dept_map[admin_dept_id].dept_name if admin_dept_id else '根节点'}")

                db.session.delete(pd)
                print(f"  删除: {pd.dept_code} - {pd.dept_name}")

            db.session.flush()

            print(f"\n[5/6] 验证迁移结果...")

            remaining_project_depts = SysDept.query.filter_by(dept_type='project').count()
            print(f"  sys_dept 剩余项目部类型记录: {remaining_project_depts} (应为 0)")

            projects_count = Project.query.count()
            projects_with_dept = Project.query.filter(Project.dept_id.isnot(None)).count()
            print(f"  projects 表总记录数: {projects_count}")
            print(f"  有 dept_id 归属的项目数: {projects_with_dept}")

            type_counts = {}
            for d in SysDept.query.all():
                t = d.dept_type
                type_counts[t] = type_counts.get(t, 0) + 1
            print(f"  sys_dept 部门类型分布:")
            for t, c in sorted(type_counts.items()):
                print(f"    {t}: {c}")

            db.session.commit()

            print(f"\n[6/6] 迁移完成！")
            print("=" * 60)

            if remaining_project_depts == 0:
                print("  迁移成功: 部门表已无项目部类型记录")
            else:
                print(f"  ⚠ 警告: 仍有 {remaining_project_depts} 条项目部类型记录")

            return True

        except Exception as e:
            db.session.rollback()
            print(f"\n❌ 迁移失败，已回滚: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == '__main__':
    success = migrate()
    sys.exit(0 if success else 1)
