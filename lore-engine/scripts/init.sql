-- Lore memory layer — PostgreSQL schema
-- Replaces MongoDB lore_memory.{threads,turns}

CREATE TABLE IF NOT EXISTS threads (
    thread_id            TEXT        PRIMARY KEY,
    user_id              TEXT        NOT NULL,
    title                TEXT        NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    turn_count           INT         NOT NULL DEFAULT 0,
    total_tokens         INT         NOT NULL DEFAULT 0,
    summary              TEXT,
    summary_up_to_sequence INT,
    status               TEXT        NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'archived', 'deleted')),
    schema_version       INT         NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_threads_user_updated
    ON threads (user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_threads_user_status
    ON threads (user_id, status);

CREATE TABLE IF NOT EXISTS turns (
    turn_id              TEXT        PRIMARY KEY,
    thread_id            TEXT        NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
    user_id              TEXT        NOT NULL,
    sequence             INT         NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_message         JSONB       NOT NULL,
    assistant_message    JSONB,
    retrieved_context    JSONB       NOT NULL DEFAULT '[]',
    tool_calls           JSONB       NOT NULL DEFAULT '[]',
    token_usage          JSONB       NOT NULL DEFAULT '{}',
    model                TEXT        NOT NULL DEFAULT '',
    latency_ms           INT,
    status               TEXT        NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'completed', 'failed')),
    schema_version       INT         NOT NULL DEFAULT 1,
    UNIQUE (thread_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_turns_thread_seq
    ON turns (thread_id, sequence);
