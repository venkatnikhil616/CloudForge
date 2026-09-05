from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon, Circle, Group
from reportlab.lib.colors import HexColor

def arrow(d, x1, y1, x2, y2, color="#64748B", width=1.2, head_len=5):
    d.add(Line(x1, y1, x2, y2, strokeColor=HexColor(color), strokeWidth=width))
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    x_head1 = x2 - head_len * math.cos(angle - math.pi / 6)
    y_head1 = y2 - head_len * math.sin(angle - math.pi / 6)
    x_head2 = x2 - head_len * math.cos(angle + math.pi / 6)
    y_head2 = y2 - head_len * math.sin(angle + math.pi / 6)
    d.add(Polygon([x2, y2, x_head1, y_head1, x_head2, y_head2], fillColor=HexColor(color), strokeColor=HexColor(color)))

def badge_box(d, x, y, w, h, title, subtitle="", bg="#3B82F6", border="#1D4ED8", text_color="#FFFFFF"):
    d.add(Rect(x, y, w, h, fillColor=HexColor(bg), strokeColor=HexColor(border), strokeWidth=1.2, rx=4, ry=4))
    if subtitle:
        d.add(String(x + w/2, y + h/2 + 3, title, fontName="Helvetica-Bold", fontSize=7.5, fillColor=HexColor(text_color), textAnchor="middle"))
        d.add(String(x + w/2, y + h/2 - 7, subtitle, fontName="Helvetica", fontSize=6.5, fillColor=HexColor(text_color), textAnchor="middle"))
    else:
        d.add(String(x + w/2, y + h/2 - 3, title, fontName="Helvetica-Bold", fontSize=8, fillColor=HexColor(text_color), textAnchor="middle"))

# 1. System Architecture Diagram
def fig_system_architecture():
    d = Drawing(520, 155)
    d.add(Rect(0, 0, 520, 155, fillColor=HexColor("#F8FAFC"), strokeColor=HexColor("#E2E8F0"), strokeWidth=1, rx=6, ry=6))
    
    badge_box(d, 15, 65, 65, 36, "Client / Web", "REST / HTTP", "#0284C7", "#0369A1")
    arrow(d, 80, 83, 105, 83)
    badge_box(d, 105, 65, 65, 36, "API Gateway", "Port :8000", "#6366F1", "#4F46E5")
    
    arrow(d, 170, 93, 195, 110)
    badge_box(d, 195, 95, 70, 32, "Auth Service", "JWT & Bcrypt", "#10B981", "#047857")
    
    arrow(d, 170, 75, 195, 57)
    badge_box(d, 195, 42, 70, 32, "Task Service", "CRUD & Publish", "#10B981", "#047857")
    
    arrow(d, 265, 58, 295, 58)
    badge_box(d, 295, 38, 75, 38, "RabbitMQ", "Topic / Priority / DLX", "#F97316", "#C2410C")
    
    arrow(d, 370, 58, 400, 58)
    badge_box(d, 400, 38, 75, 38, "Worker Pool", "Async Consumers", "#8B5CF6", "#6D28D9")
    
    badge_box(d, 295, 95, 75, 34, "PostgreSQL 16", "System of Record", "#0284C7", "#0369A1")
    badge_box(d, 400, 95, 75, 34, "Redis Cache", "Locks & Limits", "#EF4444", "#B91C1C")
    
    d.add(Line(332, 76, 332, 95, strokeColor=HexColor("#94A3B8"), strokeWidth=1))
    d.add(Line(437, 76, 437, 95, strokeColor=HexColor("#94A3B8"), strokeWidth=1))
    
    d.add(String(260, 12, "Figure 2.1: CloudTask High-Level Distributed Architecture and Service Interconnects", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#475569"), textAnchor="middle"))
    return d

# 2. Leader Election Diagram (Scheduler)
def fig_leader_election():
    d = Drawing(520, 135)
    d.add(Rect(0, 0, 520, 135, fillColor=HexColor("#F8FAFC"), strokeColor=HexColor("#E2E8F0"), strokeWidth=1, rx=6, ry=6))
    
    badge_box(d, 20, 55, 90, 40, "Scheduler Pod A", "ACQUIRES LOCK", "#10B981", "#047857")
    badge_box(d, 20, 10, 90, 35, "Scheduler Pod B", "BACKOFF / STANDBY", "#64748B", "#475569")
    
    arrow(d, 110, 75, 180, 75)
    d.add(String(145, 82, "SETNX lock", fontName="Helvetica-Bold", fontSize=7, fillColor=HexColor("#10B981"), textAnchor="middle"))
    
    badge_box(d, 180, 45, 110, 55, "Redis Cluster", "Key: leader:scheduler\nTTL: 15s (Heartbeat)", "#EF4444", "#B91C1C")
    
    arrow(d, 290, 75, 350, 75)
    d.add(String(320, 82, "Enqueues Job", fontName="Helvetica-Bold", fontSize=7, fillColor=HexColor("#0284C7"), textAnchor="middle"))
    
    badge_box(d, 350, 55, 140, 40, "RabbitMQ Broker", "cloudtask.tasks.scheduled", "#F97316", "#C2410C")
    
    d.add(String(260, 12, "Figure 6.1: Distributed Scheduler Leader Election with Redis Mutex and Auto-Renewal Heartbeats", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#475569"), textAnchor="middle"))
    return d

# 3. RabbitMQ Topology & DLX
def fig_rabbitmq_topology():
    d = Drawing(520, 145)
    d.add(Rect(0, 0, 520, 145, fillColor=HexColor("#F8FAFC"), strokeColor=HexColor("#E2E8F0"), strokeWidth=1, rx=6, ry=6))
    
    badge_box(d, 15, 60, 80, 40, "Task Service", "AMQP Publisher", "#10B981", "#047857")
    arrow(d, 95, 80, 135, 80)
    
    badge_box(d, 135, 50, 95, 55, "Topic Exchange", "cloudtask.exchange\n(Durable, Direct/Topic)", "#F97316", "#C2410C")
    
    arrow(d, 230, 90, 275, 105)
    arrow(d, 230, 75, 275, 75)
    arrow(d, 230, 60, 275, 45)
    
    badge_box(d, 275, 95, 110, 28, "Critical Queue (P10)", "x-max-priority: 10", "#EF4444", "#B91C1C")
    badge_box(d, 275, 62, 110, 28, "Normal Queue (P5)", "Standard Priority", "#3B82F6", "#1D4ED8")
    badge_box(d, 275, 30, 110, 28, "Low Queue (P1)", "Background Compute", "#64748B", "#475569")
    
    arrow(d, 385, 75, 420, 75)
    badge_box(d, 420, 55, 80, 40, "Worker Pool", "Prefetch Count: 10", "#8B5CF6", "#6D28D9")
    
    # DLX connection
    d.add(Line(460, 55, 460, 30, strokeColor=HexColor("#DC2626"), strokeWidth=1.2))
    d.add(Line(460, 30, 200, 30, strokeColor=HexColor("#DC2626"), strokeWidth=1.2))
    arrow(d, 200, 30, 150, 30, color="#DC2626")
    badge_box(d, 60, 18, 90, 24, "Dead Letter DLX", "Poison Pill Escalate", "#991B1B", "#7F1D1D")
    
    d.add(String(260, 8, "Figure 10.1: RabbitMQ Priority Queues, Topic Exchanges, and Dead-Letter Exchange (DLX)", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#475569"), textAnchor="middle"))
    return d

# 4. State Machine Diagram
def fig_state_machine():
    d = Drawing(520, 140)
    d.add(Rect(0, 0, 520, 140, fillColor=HexColor("#F8FAFC"), strokeColor=HexColor("#E2E8F0"), strokeWidth=1, rx=6, ry=6))
    
    badge_box(d, 15, 60, 55, 30, "PENDING", "Created", "#64748B", "#475569")
    arrow(d, 70, 75, 100, 75)
    
    badge_box(d, 100, 60, 55, 30, "QUEUED", "In Broker", "#3B82F6", "#1D4ED8")
    arrow(d, 155, 75, 185, 75)
    
    badge_box(d, 185, 60, 60, 30, "RUNNING", "Executing", "#F59E0B", "#D97706")
    
    # Branching from running
    arrow(d, 245, 85, 290, 105)
    badge_box(d, 290, 95, 65, 28, "SUCCESS", "100% Done", "#10B981", "#047857")
    
    arrow(d, 245, 65, 290, 50)
    badge_box(d, 290, 35, 65, 28, "FAILED", "Error Caught", "#EF4444", "#B91C1C")
    
    # Retry cycle
    arrow(d, 355, 45, 390, 45)
    badge_box(d, 390, 32, 55, 26, "RETRY", "Exp Backoff", "#8B5CF6", "#6D28D9")
    d.add(Line(417, 58, 417, 85, strokeColor=HexColor("#8B5CF6"), strokeWidth=1))
    arrow(d, 417, 85, 128, 90, color="#8B5CF6")
    
    # DLQ from retry maxed
    arrow(d, 417, 32, 417, 15)
    d.add(Line(417, 15, 450, 15, strokeColor=HexColor("#DC2626"), strokeWidth=1.2))
    badge_box(d, 450, 20, 60, 35, "DEAD_LETTER", "Max Attempts", "#7F1D1D", "#450A0A")
    
    d.add(String(260, 6, "Figure 11.1: Formal Distributed Task Finite State Machine and Failure Escalation Transitions", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#475569"), textAnchor="middle"))
    return d

# 5. Two-Tier Idempotency Sequence
def fig_two_tier_idempotency():
    d = Drawing(520, 140)
    d.add(Rect(0, 0, 520, 140, fillColor=HexColor("#F8FAFC"), strokeColor=HexColor("#E2E8F0"), strokeWidth=1, rx=6, ry=6))
    
    badge_box(d, 20, 90, 90, 32, "1. Request Ingestion", "Client Task Header", "#0284C7", "#0369A1")
    arrow(d, 110, 106, 150, 106)
    
    badge_box(d, 150, 85, 140, 44, "Tier 1: Redis Mutex Lock", "SET lock:task:{id} NX EX 60s\nChecks if already in-flight", "#EF4444", "#B91C1C")
    arrow(d, 290, 106, 330, 106)
    
    badge_box(d, 330, 85, 160, 44, "Tier 2: PostgreSQL Constraint", "UNIQUE(idempotency_key)\nAuthoritative state verification", "#10B981", "#047857")
    
    # Outcomes below
    d.add(Line(220, 85, 220, 50, strokeColor=HexColor("#94A3B8"), strokeWidth=1))
    arrow(d, 220, 50, 160, 30)
    badge_box(d, 70, 15, 120, 28, "Key Exists in Cache", "Return Cached Result / 409", "#64748B", "#475569")
    
    d.add(Line(410, 85, 410, 50, strokeColor=HexColor("#94A3B8"), strokeWidth=1))
    arrow(d, 410, 50, 410, 43)
    badge_box(d, 340, 15, 140, 28, "Execute Task Safely", "Lock Released on ACK", "#8B5CF6", "#6D28D9")
    
    d.add(String(260, 4, "Figure 13.1: Two-Tier Distributed Idempotency Mechanism Combining Ephemeral Redis and Authoritative PostgreSQL", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#475569"), textAnchor="middle"))
    return d

# 6. Retry Timeline & Exponential Backoff
def fig_exponential_backoff():
    d = Drawing(520, 130)
    d.add(Rect(0, 0, 520, 130, fillColor=HexColor("#F8FAFC"), strokeColor=HexColor("#E2E8F0"), strokeWidth=1, rx=6, ry=6))
    
    # Timeline bar
    d.add(Line(30, 65, 480, 65, strokeColor=HexColor("#94A3B8"), strokeWidth=2))
    
    # Nodes
    points = [
        (40, "Initial Execution", "T=0s", "#3B82F6"),
        (130, "Attempt 1 Failure", "Delay 5s", "#F59E0B"),
        (230, "Attempt 2 Failure", "Delay 15s", "#F97316"),
        (350, "Attempt 3 Failure", "Delay 45s", "#EF4444"),
        (460, "Escalate to DLQ", "Final Poison Pill", "#7F1D1D")
    ]
    for x, title, sub, col in points:
        d.add(Circle(x, 65, 5, fillColor=HexColor(col), strokeColor=HexColor("#FFFFFF"), strokeWidth=1.5))
        d.add(String(x, 80, title, fontName="Helvetica-Bold", fontSize=7, fillColor=HexColor(col), textAnchor="middle"))
        d.add(String(x, 50, sub, fontName="Helvetica", fontSize=6.5, fillColor=HexColor("#475569"), textAnchor="middle"))
        
    d.add(String(260, 20, "Controlled Exponential Backoff Formula:  Delay = Base (5s) * (3 ^ Attempt) + Full Random Jitter", fontName="Helvetica-Bold", fontSize=8, fillColor=HexColor("#1E293B"), textAnchor="middle"))
    d.add(String(260, 8, "Figure 14.1: Exponential Backoff Retry Progression and Automatic Dead-Letter Queue Escalation", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#475569"), textAnchor="middle"))
    return d

# 7. Kubernetes Cluster Topology
def fig_k8s_topology():
    d = Drawing(520, 150)
    d.add(Rect(0, 0, 520, 150, fillColor=HexColor("#F8FAFC"), strokeColor=HexColor("#E2E8F0"), strokeWidth=1, rx=6, ry=6))
    
    # Ingress
    badge_box(d, 20, 105, 90, 32, "NGINX Ingress", "SSL / TLS Termination", "#326CE5", "#1E40AF")
    arrow(d, 110, 121, 140, 121)
    
    # Stateless Deployments
    d.add(Rect(140, 75, 170, 65, fillColor=HexColor("#EFF6FF"), strokeColor=HexColor("#93C5FD"), strokeWidth=1, rx=4, ry=4))
    d.add(String(225, 128, "Stateless Workloads (Deployments)", fontName="Helvetica-Bold", fontSize=7, fillColor=HexColor("#1E40AF"), textAnchor="middle"))
    badge_box(d, 145, 85, 75, 30, "API Gateway", "HPA Scaled", "#3B82F6", "#1D4ED8")
    badge_box(d, 225, 85, 80, 30, "Worker Pods (1..N)", "HPA Queue Depth", "#8B5CF6", "#6D28D9")
    
    # StatefulSets
    d.add(Rect(330, 75, 175, 65, fillColor=HexColor("#F0FDF4"), strokeColor=HexColor("#86EFAC"), strokeWidth=1, rx=4, ry=4))
    d.add(String(417, 128, "Stateful Infrastructure (StatefulSets)", fontName="Helvetica-Bold", fontSize=7, fillColor=HexColor("#166534"), textAnchor="middle"))
    badge_box(d, 335, 85, 50, 30, "PostgreSQL", "PVC (SSD)", "#0284C7", "#0369A1")
    badge_box(d, 390, 85, 55, 30, "RabbitMQ", "PVC (Durable)", "#F97316", "#C2410C")
    badge_box(d, 450, 85, 50, 30, "Redis", "PVC (AOF)", "#EF4444", "#B91C1C")
    
    # NetworkPolicy security bar
    d.add(Rect(30, 30, 460, 26, fillColor=HexColor("#FEF2F2"), strokeColor=HexColor("#FCA5A5"), strokeWidth=1, rx=4, ry=4))
    d.add(String(260, 42, "Kubernetes NetworkPolicies: Default-Deny Ingress/Egress Isolation Across Namespaces", fontName="Helvetica-Bold", fontSize=7.5, fillColor=HexColor("#991B1B"), textAnchor="middle"))
    
    d.add(String(260, 10, "Figure 17.1: Production Kubernetes Pod Topology, StatefulSets with Persistent Volumes, and Network Isolation", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#475569"), textAnchor="middle"))
    return d

# 8. CI/CD and GitOps Pipeline
def fig_cicd_pipeline():
    d = Drawing(520, 135)
    d.add(Rect(0, 0, 520, 135, fillColor=HexColor("#F8FAFC"), strokeColor=HexColor("#E2E8F0"), strokeWidth=1, rx=6, ry=6))
    
    badge_box(d, 15, 65, 65, 34, "Git Push", "Branch: main", "#0F172A", "#000000")
    arrow(d, 80, 82, 105, 82)
    
    badge_box(d, 105, 65, 75, 34, "GitHub Actions", "CI Triggered", "#2563EB", "#1D4ED8")
    arrow(d, 180, 82, 205, 82)
    
    badge_box(d, 205, 65, 85, 34, "Automated Tests", "Unit & Integration", "#10B981", "#047857")
    arrow(d, 290, 82, 315, 82)
    
    badge_box(d, 315, 65, 85, 34, "Docker & Trivy", "Scan & Build", "#F59E0B", "#D97706")
    arrow(d, 400, 82, 425, 82)
    
    badge_box(d, 425, 65, 80, 34, "Argo CD", "GitOps Sync", "#EF4444", "#B91C1C")
    
    # Destination cluster
    arrow(d, 465, 65, 465, 45)
    badge_box(d, 395, 18, 110, 24, "Kubernetes Production", "Rolling Zero-Downtime", "#326CE5", "#1E40AF")
    
    d.add(String(260, 6, "Figure 21.1: Automated CI Pipeline (GitHub Actions) and Continuous GitOps Synchronization (Argo CD)", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#475569"), textAnchor="middle"))
    return d

# 9. Observability Pipeline
def fig_observability():
    d = Drawing(520, 135)
    d.add(Rect(0, 0, 520, 135, fillColor=HexColor("#F8FAFC"), strokeColor=HexColor("#E2E8F0"), strokeWidth=1, rx=6, ry=6))
    
    badge_box(d, 20, 65, 80, 40, "CloudTask Apps", "Gateway & Workers", "#8B5CF6", "#6D28D9")
    
    arrow(d, 100, 90, 140, 105)
    badge_box(d, 140, 92, 105, 30, "Prometheus Exporter", "Custom Metrics /metrics", "#F97316", "#C2410C")
    
    arrow(d, 100, 70, 140, 55)
    badge_box(d, 140, 42, 105, 30, "Structured Logs", "Correlation ID / JSON", "#10B981", "#047857")
    
    arrow(d, 245, 107, 285, 107)
    badge_box(d, 285, 92, 90, 30, "Prometheus Server", "Alert Rules & TSDB", "#F97316", "#C2410C")
    
    arrow(d, 245, 57, 285, 57)
    badge_box(d, 285, 42, 90, 30, "Grafana Loki", "Log Indexing", "#0284C7", "#0369A1")
    
    arrow(d, 375, 107, 415, 85)
    arrow(d, 375, 57, 415, 75)
    badge_box(d, 415, 60, 85, 40, "Grafana UI", "Unified Dashboards", "#F59E0B", "#D97706")
    
    d.add(String(260, 8, "Figure 23.1: End-to-End Observability Pipeline Integrating Metrics, Logs, and Visual Dashboards", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#475569"), textAnchor="middle"))
    return d

# 10. Dashboard UI Mockup Diagram
def fig_dashboard_mockup():
    d = Drawing(520, 145)
    d.add(Rect(0, 0, 520, 145, fillColor=HexColor("#0F172A"), strokeColor=HexColor("#334155"), strokeWidth=1.5, rx=6, ry=6))
    
    # Top navbar
    d.add(Rect(5, 115, 510, 25, fillColor=HexColor("#1E293B"), strokeColor=HexColor("#334155"), strokeWidth=1, rx=3, ry=3))
    d.add(String(20, 126, "CloudTask Realtime Operations Dashboard", fontName="Helvetica-Bold", fontSize=8.5, fillColor=HexColor("#38BDF8")))
    d.add(String(410, 126, "Operational | Live Render", fontName="Helvetica-Bold", fontSize=7.5, fillColor=HexColor("#4ADE80")))
    
    # 4 metric cards
    cards = [
        (15, "Pending", "0", "#94A3B8"),
        (140, "Processing", "1", "#38BDF8"),
        (265, "Completed", "42", "#4ADE80"),
        (390, "DLQ / Failed", "0", "#F87171")
    ]
    for x, label, val, col in cards:
        d.add(Rect(x, 70, 115, 38, fillColor=HexColor("#1E293B"), strokeColor=HexColor("#334155"), strokeWidth=1, rx=4, ry=4))
        d.add(String(x + 10, 94, label, fontName="Helvetica", fontSize=7, fillColor=HexColor("#94A3B8")))
        d.add(String(x + 10, 78, val, fontName="Helvetica-Bold", fontSize=12, fillColor=HexColor(col)))
        
    # Quick actions bar
    d.add(Rect(15, 25, 490, 36, fillColor=HexColor("#1E293B"), strokeColor=HexColor("#334155"), strokeWidth=1, rx=4, ry=4))
    d.add(Rect(25, 31, 85, 24, fillColor=HexColor("#0284C7"), strokeColor=HexColor("#0369A1"), strokeWidth=1, rx=3, ry=3))
    d.add(String(67, 41, "New Task", fontName="Helvetica-Bold", fontSize=7.5, fillColor=HexColor("#FFFFFF"), textAnchor="middle"))
    
    d.add(Rect(120, 31, 85, 24, fillColor=HexColor("#10B981"), strokeColor=HexColor("#047857"), strokeWidth=1, rx=3, ry=3))
    d.add(String(162, 41, "Export CSV", fontName="Helvetica-Bold", fontSize=7.5, fillColor=HexColor("#FFFFFF"), textAnchor="middle"))
    
    d.add(Rect(215, 31, 85, 24, fillColor=HexColor("#EF4444"), strokeColor=HexColor("#B91C1C"), strokeWidth=1, rx=3, ry=3))
    d.add(String(257, 41, "Replay All (DLQ)", fontName="Helvetica-Bold", fontSize=7.5, fillColor=HexColor("#FFFFFF"), textAnchor="middle"))
    
    d.add(String(320, 41, "Realtime Polling (3s interval)", fontName="Helvetica", fontSize=7.5, fillColor=HexColor("#94A3B8")))
    
    d.add(String(260, 8, "Figure 28.1: Live Cloud Operations Dashboard User Interface and Task Dispatch Engine", fontName="Helvetica-Oblique", fontSize=7.5, fillColor=HexColor("#94A3B8"), textAnchor="middle"))
    return d

print("report_diagrams.py loaded and verified!")
