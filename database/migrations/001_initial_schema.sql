-- CloudTask Initial Database Schema

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Users Table
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(36) PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user' NOT NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- 2. Tasks Table
CREATE TABLE IF NOT EXISTS tasks (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    task_type VARCHAR(100) NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    status VARCHAR(50) DEFAULT 'PENDING' NOT NULL,
    priority INTEGER DEFAULT 5 NOT NULL,
    max_retries INTEGER DEFAULT 4 NOT NULL,
    current_attempt INTEGER DEFAULT 0 NOT NULL,
    timeout_seconds INTEGER DEFAULT 300 NOT NULL,
    progress INTEGER DEFAULT 0 NOT NULL,
    depends_on JSONB DEFAULT '[]'::jsonb NOT NULL,
    trace_id VARCHAR(64),
    idempotency_key VARCHAR(255) UNIQUE,
    result JSONB,
    error_message VARCHAR(2000),
    scheduled_at TIMESTAMP WITH TIME ZONE,
    webhook_url VARCHAR(1000),
    delay_seconds INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks(status, priority);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_trace_id ON tasks(trace_id);

-- 3. Task Attempts Table
CREATE TABLE IF NOT EXISTS task_attempts (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL,
    worker_id VARCHAR(255) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITH TIME ZONE,
    duration_ms INTEGER,
    trace_id VARCHAR(64),
    error_message VARCHAR(2000),
    stack_trace VARCHAR(4000)
);

CREATE INDEX IF NOT EXISTS idx_attempts_task_id ON task_attempts(task_id);

-- 4. Task Schedules Table
CREATE TABLE IF NOT EXISTS task_schedules (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    task_type VARCHAR(100) NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb NOT NULL,
    cron_expression VARCHAR(100),
    interval_seconds INTEGER,
    is_enabled BOOLEAN DEFAULT TRUE NOT NULL,
    priority INTEGER DEFAULT 5 NOT NULL,
    next_run_at TIMESTAMP WITH TIME ZONE,
    last_run_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_schedules_user_id ON task_schedules(user_id);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled_next ON task_schedules(is_enabled, next_run_at);

-- 5. Notifications Table
CREATE TABLE IF NOT EXISTS notifications (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    task_id VARCHAR(36),
    event_type VARCHAR(100) NOT NULL,
    channel VARCHAR(50) DEFAULT 'email' NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    status VARCHAR(50) DEFAULT 'SENT' NOT NULL,
    message VARCHAR(2000) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_task_id ON notifications(task_id);
