import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure monorepo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import select

from pkg.database import AsyncSessionLocal, Base, engine
from pkg.logger import get_logger
from pkg.models import Task, TaskSchedule, TaskStatus, User
from pkg.security import hash_password

logger = get_logger("seed")


async def seed() -> None:
    """Seeds initial test users, demo tasks, and schedules into PostgreSQL with connection retry."""
    connected = False
    for attempt in range(1, 10):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            connected = True
            break
        except Exception as e:
            logger.warning(f"Database connection attempt {attempt}/9 failed: {e}. Retrying in 3s...")
            await asyncio.sleep(3)

    if not connected:
        logger.error("Could not connect to database after retries. Proceeding anyway.")
        return

    async with AsyncSessionLocal() as session:
        try:
            # 1. Admin User
            stmt = select(User).where(User.email == "admin@cloudtask.dev")
            admin_user = (await session.execute(stmt)).scalar_one_or_none()
            if not admin_user:
                admin_user = User(
                    id=str(uuid.uuid4()),
                    email="admin@cloudtask.dev",
                    hashed_password=hash_password("AdminSecurePass123!"),
                    full_name="CloudTask Admin",
                    role="admin",
                    is_active=True,
                )
                session.add(admin_user)
                await session.commit()
                logger.info("Created default admin user.")

            # 2. Demo User
            demo_stmt = select(User).where(User.email == "demo@cloudtask.dev")
            demo_user = (await session.execute(demo_stmt)).scalar_one_or_none()
            if not demo_user:
                demo_user = User(
                    id=str(uuid.uuid4()),
                    email="demo@cloudtask.dev",
                    hashed_password=hash_password("DemoSecurePass123!"),
                    full_name="Demo Developer",
                    role="user",
                    is_active=True,
                )
                session.add(demo_user)
                await session.commit()
                logger.info("Created default demo user.")

            # 3. Initial Sample Task
            task_stmt = select(Task).limit(1)
            has_tasks = (await session.execute(task_stmt)).scalar_one_or_none()
            if not has_tasks:
                sample_task = Task(
                    id=str(uuid.uuid4()),
                    user_id=demo_user.id,
                    title="Process monthly report",
                    task_type="report_generation",
                    payload={"month": "August", "year": 2026, "format": "PDF"},
                    status=TaskStatus.QUEUED,
                    priority=8,
                    max_retries=4,
                )
                session.add(sample_task)

            # 4. Initial Sample Schedule
            sched_stmt = select(TaskSchedule).limit(1)
            has_sched = (await session.execute(sched_stmt)).scalar_one_or_none()
            if not has_sched:
                sample_schedule = TaskSchedule(
                    id=str(uuid.uuid4()),
                    user_id=demo_user.id,
                    title="Nightly Database Health Cleanup",
                    task_type="system_cleanup",
                    payload={"target": "temp_files", "older_than_days": 7},
                    cron_expression="0 2 * * *",
                    is_enabled=True,
                    next_run_at=datetime.now(timezone.utc) + timedelta(days=1),
                )
                session.add(sample_schedule)

            await session.commit()
            logger.info("Seed verification and initialization completed.")
        except Exception as e:
            logger.warning(f"Error seeding data: {e}")


if __name__ == "__main__":
    asyncio.run(seed())
