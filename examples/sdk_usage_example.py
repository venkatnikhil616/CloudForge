"""
Example demonstrating how external applications use the CloudTask Python SDK
to dispatch asynchronous tasks and orchestrate DAG workflows.
"""
from sdk.cloudtask import CloudTaskClient

# 1. Initialize client
client = CloudTaskClient(
    base_url="https://cloudtask-platform.onrender.com",
    email="admin@cloudtask.dev",
    password="AdminSecurePass123!"
)

# 2. Define distributed task using @client.task decorator
@client.task(task_type="report_generation", priority=9)
def generate_audit_report(month: str, year: int):
    pass

print("=== 1. Dispatching Asynchronous Task ===")
promise_a = generate_audit_report.delay(month="September", year=2026, format="PDF")
print(f"Task dispatched with ID: {promise_a.task_id}")

print("\n=== 2. Orchestrating Dependent DAG Workflow ===")
# Task B depends on Task A finishing first
promise_b = client.submit_task(
    title="Dispatch Audit Email to Stakeholders",
    task_type="email_dispatch",
    payload={"to": "ceo@enterprise.com", "report_id": promise_a.task_id},
    priority=10,
    depends_on=[promise_a.task_id]  # DAG dependency!
)
print(f"Dependent Task B queued (waiting on Task A): {promise_b.task_id}")

print("\nSDK workflow submission verified!")
