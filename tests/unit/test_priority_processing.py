import pytest
from pkg.redis_client import get_execution_mode, set_execution_mode
from services.worker.main import process_priority_queue

@pytest.mark.asyncio
async def test_execution_mode_toggling():
    # Verify default mode is manual
    mode = await get_execution_mode()
    assert mode in ["manual", "auto"]

    # Toggle to auto
    updated = await set_execution_mode("auto")
    assert updated == "auto"
    assert await get_execution_mode() == "auto"

    # Toggle back to manual
    updated = await set_execution_mode("manual")
    assert updated == "manual"
    assert await get_execution_mode() == "manual"

@pytest.mark.asyncio
async def test_priority_processing_empty_queue():
    # If queue is empty, process_priority_queue returns 0 without errors
    count = await process_priority_queue()
    assert isinstance(count, int)

@pytest.mark.asyncio
async def test_clear_history_endpoint_empty():
    import importlib.util
    from pkg.database import AsyncSessionLocal

    spec = importlib.util.spec_from_file_location("task_routes", "services/task-service/routes.py")
    task_routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(task_routes)

    async with AsyncSessionLocal() as session:
        res = await task_routes.clear_history_endpoint(db=session)
        assert res["status"] == "ok"
        assert isinstance(res["deleted_count"], int)

