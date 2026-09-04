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

print("\n=== 3. High-Throughput Batch Task Ingestion (AWS SQS Pattern) ===")
batch_result = client.submit_batch([
    {
        "title": f"Batch ETL Job #{i}",
        "task_type": "data_processing",
        "payload": {"batch_id": i, "records": 500},
        "priority": 7,
        "delay_seconds": 10 if i % 2 == 0 else 0,
        "webhook_url": "https://api.enterprise.com/hooks/task-completion",
    }
    for i in range(1, 4)
])
print(f"Batch dispatched: {batch_result['successful_count']}/{batch_result['total_submitted']} tasks queued")

print("\n=== 4. Compliance & Audit Export (SOC2 / ISO27001) ===")
csv_bytes = client.export_audit(format="csv")
print(f"Exported audit log CSV ({len(csv_bytes)} bytes)")

print("\nSDK workflow submission verified!")
