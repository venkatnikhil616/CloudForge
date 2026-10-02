from pkg.models.task import TaskStatus


def test_task_status_enum_values():
    assert TaskStatus.PENDING == "PENDING"
    assert TaskStatus.QUEUED == "QUEUED"
    assert TaskStatus.RUNNING == "RUNNING"
    assert TaskStatus.SUCCESS == "SUCCESS"
    assert TaskStatus.FAILED == "FAILED"
    assert TaskStatus.RETRY == "RETRY"
    assert TaskStatus.CANCELLED == "CANCELLED"
    assert TaskStatus.DEAD_LETTERED == "DEAD_LETTERED"
