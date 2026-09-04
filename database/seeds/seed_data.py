import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from pkg.database import AsyncSessionLocal, engine, Base
from pkg.models import User, Task, TaskStatus, TaskSchedule
from pkg.security import hash_password
from pkg.logger import get_logger

logger = get_logger("seed")


async def seed() -> None:
    """Seeds initial test users, demo tasks, and schedules into PostgreSQL."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Check if admin user exists
        stmt = select(User).where(User.email == "admin@cloudtask.dev")
        result = await session.execute(stmt)
        existing_user = result.scalar_one_or_none()

        if not existing_user:
            admin_user = User(
                id=str(uuid.uuid4()),
                email="admin@cloudtask.dev",
                hashed_password=hash_password("AdminSecurePass123!"),
                full_name="CloudTask Admin",
                role="admin",
                is_active=True,
            )
            demo_user = User(
                id=str(uuid.uuid4()),
                email="demo@cloudtask.dev",
                hashed_password=hash_password("DemoSecurePass123!"),
                full_name="Demo Developer",
                role="user",
                is_active=True,
            )
            session.add_all([admin_user, demo_user])
            await session.commit()
            logger.info("Created default admin and demo users.")

            # Add sample task
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

            # Add sample schedule
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

            session.add_all([sample_task, sample_schedule])
            await session.commit()
            logger.info("Created initial sample task and schedule.")
        else:
            logger.info("Seed data already present. Skipping.")


if __name__ == "__main__":
    asyncio.run(seed())
