
import pytest

from services.worker.tasks.registry import handle_data_processing, handle_report_generation


@pytest.mark.asyncio
async def test_task_handlers_execution():
    report_result = await handle_report_generation({"month": "August", "format": "PDF"})
    assert report_result["status"] == "success"
    assert "report_august.pdf" in report_result["output_file"]
    assert report_result["records_processed"] == 1420

    data_result = await handle_data_processing({"batch_size": 250})
    assert data_result["status"] == "success"
    assert data_result["items_ingested"] == 250
