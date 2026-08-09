import warnings

import novacode.task as task_api
from novacode.task.manager import AgentRun, AgentRunManager


def test_old_task_exports_delegate_to_agent_run_types_with_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old_run = task_api.BackgroundTask
        old_manager = task_api.Manager

    assert old_run is AgentRun
    assert old_manager is AgentRunManager
    assert len(caught) == 2
    assert all(item.category is DeprecationWarning for item in caught)
