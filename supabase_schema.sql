-- ============================================================
-- LLM Visibility Monitor — Supabase Schema
-- ============================================================

-- 1. Progetti / clienti
CREATE TABLE IF NOT EXISTS lvm_projects (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    language        TEXT DEFAULT 'it',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- 2. Keyword seed
CREATE TABLE IF NOT EXISTS lvm_keywords (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id      UUID REFERENCES lvm_projects(id) ON DELETE CASCADE,
    keyword         TEXT NOT NULL,
    search_volume   INT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(project_id, keyword)
);

-- 3. Domande PAA e query fan-out espanse
CREATE TABLE IF NOT EXISTS lvm_expanded_queries (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id      UUID REFERENCES lvm_projects(id) ON DELETE CASCADE,
    source_keyword_id UUID REFERENCES lvm_keywords(id) ON DELETE CASCADE,
    query_text      TEXT NOT NULL,
    query_type      TEXT NOT NULL CHECK (query_type IN ('paa', 'fanout')),
    is_selected     BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(project_id, query_text)
);

-- 4. Configurazione run
CREATE TABLE IF NOT EXISTS lvm_run_configs (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id      UUID REFERENCES lvm_projects(id) ON DELETE CASCADE,
    iterations_per_run  INT DEFAULT 3,
    daily_runs      INT DEFAULT 1,
    language        TEXT DEFAULT 'it',
    models_llm      JSONB DEFAULT '["chatgpt","claude","gemini","perplexity"]'::jsonb,
    models_serp     JSONB DEFAULT '["ai_overview","ai_mode"]'::jsonb,
    schedule_hour   INT DEFAULT 8,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- 5. Run (singola esecuzione)
CREATE TABLE IF NOT EXISTS lvm_runs (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id      UUID REFERENCES lvm_projects(id) ON DELETE CASCADE,
    config_id       UUID REFERENCES lvm_run_configs(id),
    status          TEXT DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','cancelled')),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    total_calls     INT DEFAULT 0,
    completed_calls INT DEFAULT 0,
    error_log       TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 6. Risposte LLM (1 riga per chiamata API)
CREATE TABLE IF NOT EXISTS lvm_responses (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    run_id          UUID REFERENCES lvm_runs(id) ON DELETE CASCADE,
    project_id      UUID REFERENCES lvm_projects(id) ON DELETE CASCADE,
    query_id        UUID REFERENCES lvm_expanded_queries(id),
    query_text      TEXT NOT NULL,
    platform        TEXT NOT NULL,  -- chatgpt, claude, gemini, perplexity, ai_overview, ai_mode
    model_used      TEXT,
    iteration       INT DEFAULT 1,
    response_text   TEXT,
    response_time_s FLOAT,
    error           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 7. Brand menzionati (1 riga per brand per risposta)
CREATE TABLE IF NOT EXISTS lvm_brand_mentions (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    response_id     UUID REFERENCES lvm_responses(id) ON DELETE CASCADE,
    run_id          UUID REFERENCES lvm_runs(id) ON DELETE CASCADE,
    project_id      UUID REFERENCES lvm_projects(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,
    brand           TEXT NOT NULL,
    mention_count   INT DEFAULT 1,
    position_first  INT,  -- posizione (char) della prima menzione
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 8. Fonti / URL citati (1 riga per URL per risposta)
CREATE TABLE IF NOT EXISTS lvm_source_citations (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    response_id     UUID REFERENCES lvm_responses(id) ON DELETE CASCADE,
    run_id          UUID REFERENCES lvm_runs(id) ON DELETE CASCADE,
    project_id      UUID REFERENCES lvm_projects(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,
    url             TEXT NOT NULL,
    domain          TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 9. Metriche aggregate per run (precalcolate)
CREATE TABLE IF NOT EXISTS lvm_run_metrics (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    run_id          UUID REFERENCES lvm_runs(id) ON DELETE CASCADE,
    project_id      UUID REFERENCES lvm_projects(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,
    metric_type     TEXT NOT NULL,  -- brand_count, source_count, jaccard_intra, jaccard_cross, sentiment_avg
    metric_value    FLOAT,
    metric_detail   JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 10. Utenti (auto-registrati al primo login Google)
CREATE TABLE IF NOT EXISTS lvm_users (
    email           TEXT PRIMARY KEY,
    display_name    TEXT,
    avatar_url      TEXT,
    first_login     TIMESTAMPTZ DEFAULT now(),
    last_login      TIMESTAMPTZ DEFAULT now()
);

-- 11. Assegnazione utente → progetto
CREATE TABLE IF NOT EXISTS lvm_user_projects (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_email      TEXT REFERENCES lvm_users(email) ON DELETE CASCADE,
    project_id      UUID REFERENCES lvm_projects(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_email, project_id)
);

-- Indici per performance
CREATE INDEX IF NOT EXISTS idx_lvm_keywords_project ON lvm_keywords(project_id);
CREATE INDEX IF NOT EXISTS idx_lvm_expanded_project ON lvm_expanded_queries(project_id);
CREATE INDEX IF NOT EXISTS idx_lvm_runs_project ON lvm_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_lvm_responses_run ON lvm_responses(run_id);
CREATE INDEX IF NOT EXISTS idx_lvm_responses_project ON lvm_responses(project_id);
CREATE INDEX IF NOT EXISTS idx_lvm_brand_run ON lvm_brand_mentions(run_id);
CREATE INDEX IF NOT EXISTS idx_lvm_brand_project ON lvm_brand_mentions(project_id);
CREATE INDEX IF NOT EXISTS idx_lvm_source_run ON lvm_source_citations(run_id);
CREATE INDEX IF NOT EXISTS idx_lvm_metrics_run ON lvm_run_metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_lvm_user_projects_email ON lvm_user_projects(user_email);
CREATE INDEX IF NOT EXISTS idx_lvm_user_projects_project ON lvm_user_projects(project_id);
