"""
THEN helpers for task result assertions.
"""


def all_tasks_should_have_completed_successfully(result: dict):
    """THEN all tasks should have completed without errors."""
    if result.get('error'):
        raise AssertionError(f"Task runner encountered an error: {result['error']}")
    
    failed = result.get('failed', [])
    if failed:
        failures = [f"Task '{task['task']}' failed: {task['error']}" 
                   if isinstance(task, dict) else f"Failed task: {task}"
                   for task in failed]
        raise AssertionError(f"Some tasks failed: {'; '.join(failures)}")


def tasks_should_have_succeeded(expected_tasks: list, result: dict):
    """THEN specific tasks should have succeeded."""
    succeeded = result.get('succeeded', [])
    
    for task_name in expected_tasks:
        if task_name not in succeeded:
            raise AssertionError(f"Task '{task_name}' was expected to succeed but was not in succeeded list: {succeeded}")