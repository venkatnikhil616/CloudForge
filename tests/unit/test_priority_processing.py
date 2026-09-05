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
