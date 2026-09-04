# CloudTask API Specification

All endpoints are served through the unified API Gateway at port `8000`.

## Base URL
`http://localhost:8000/api/v1`

---

## 1. Authentication Endpoints

### Register User
- **Endpoint**: `POST /auth/register`
- **Body**:
  ```json
  {
    "email": "developer@cloudtask.dev",
    "password": "SecurePassword123!",
    "full_name": "Dev User"
  }
  ```
- **Response**: `201 Created`

### Login & Obtain JWT Token
- **Endpoint**: `POST /auth/login`
- **Body**:
  ```json
  {
    "email": "developer@cloudtask.dev",
    "password": "SecurePassword123!"
  }
  ```
- **Response**: `200 OK`
  ```json
  {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "expires_in_minutes": 60
  }
  ```

---

## 2. Task Management Endpoints

### Create Task
- **Endpoint**: `POST /tasks`
- **Headers**: `Authorization: Bearer <token>`
- **Body**:
  ```json
  {
    "title": "Quarterly Data Processing",
    "task_type": "data_processing",
    "payload": { "batch_size": 500 },
    "priority": 8,
    "max_retries": 4,
    "timeout_seconds": 300,
    "idempotency_key": "custom-uuid-key-001"
  }
  ```
- **Response**: `201 Created`

### List Tasks
- **Endpoint**: `GET /tasks?page=1&limit=20&status_filter=QUEUED`
- **Headers**: `Authorization: Bearer <token>`

### Cancel Task
- **Endpoint**: `POST /tasks/{task_id}/cancel`
- **Headers**: `Authorization: Bearer <token>`

### Retry Failed Task
- **Endpoint**: `POST /tasks/{task_id}/retry`
- **Headers**: `Authorization: Bearer <token>`
