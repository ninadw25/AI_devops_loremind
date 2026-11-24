-- Bootstrap extensions for lore-chronograph database
-- Runs once on first container start (via docker-entrypoint-initdb.d).
-- The Python store also calls CREATE EXTENSION IF NOT EXISTS on connect,
-- so this is belt-and-suspenders — not required, but speeds up first connect.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";      -- pgvector: ANN similarity search
-- PostGIS removed: the base image is pgvector/pgvector:pg16 (multi-arch incl. arm64);
-- PostGIS was declared "future use" only and has no arm64 image. Re-add if needed.
CREATE EXTENSION IF NOT EXISTS "pg_trgm";     -- trigram index for ILIKE searches
