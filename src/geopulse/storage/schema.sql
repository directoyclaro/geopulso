-- Esquema GeoPulse (DuckDB)
-- Bronze se guarda como JSON/Parquet; estas tablas son el indice analitico (silver/gold).

CREATE TABLE IF NOT EXISTS sources (
    source_id      VARCHAR PRIMARY KEY,
    name           VARCHAR,
    platform       VARCHAR,
    handle         VARCHAR,
    url            VARCHAR,
    type           VARCHAR,
    municipio      VARCHAR,
    region         VARCHAR,
    verified       BOOLEAN DEFAULT FALSE,
    status         VARCHAR DEFAULT 'activa',
    score          DOUBLE DEFAULT 0.0,
    discovered_at  TIMESTAMP DEFAULT now(),
    last_checked   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
    raw_id        VARCHAR PRIMARY KEY,
    source_id     VARCHAR,
    platform      VARCHAR,
    collected_at  TIMESTAMP,
    posted_at     TIMESTAMP,
    text          VARCHAR,
    language      VARCHAR,
    author_hash   VARCHAR,
    hashtags      VARCHAR[],
    keywords      VARCHAR[],
    location_text VARCHAR,
    url           VARCHAR,
    simhash       UBIGINT,
    raw_path      VARCHAR
);

CREATE TABLE IF NOT EXISTS posts_geo (
    raw_id      VARCHAR PRIMARY KEY,
    municipality VARCHAR,
    distrito     VARCHAR,
    province     VARCHAR,
    country      VARCHAR,
    confidence   DOUBLE,
    method       VARCHAR,
    updated_at   TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS posts_enriched (
    raw_id      VARCHAR PRIMARY KEY,
    topics      VARCHAR[],
    sentiment   VARCHAR,
    intention   VARCHAR,
    summary     VARCHAR,
    method      VARCHAR,
    model       VARCHAR,
    enriched_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS preferences (
    zona         VARCHAR,
    categoria    VARCHAR,
    peso         BIGINT,
    ventana_start DATE,
    ventana_end   DATE,
    updated_at   TIMESTAMP DEFAULT now(),
    PRIMARY KEY (zona, categoria, ventana_start, ventana_end)
);

CREATE TABLE IF NOT EXISTS trends (
    zona       VARCHAR,
    tema       VARCHAR,
    ventana    DATE,
    volumen    INTEGER,
    esperado   DOUBLE,
    zscore     DOUBLE,
    ratio      DOUBLE,
    es_burst   BOOLEAN,
    updated_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (zona, tema, ventana)
);

CREATE TABLE IF NOT EXISTS keywords (
    term       VARCHAR PRIMARY KEY,
    source     VARCHAR DEFAULT 'manual',
    weight     DOUBLE DEFAULT 1.0,
    active     BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS locations (
    location_id VARCHAR PRIMARY KEY,
    name        VARCHAR,
    level       VARCHAR,
    municipio   VARCHAR,
    province    VARCHAR DEFAULT 'Bahoruco',
    country     VARCHAR DEFAULT 'DO',
    lat         DOUBLE,
    lon         DOUBLE,
    aliases     VARCHAR[],
    source      VARCHAR DEFAULT 'manual',
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS topics (
    topic_id    VARCHAR PRIMARY KEY,
    label       VARCHAR,
    taxonomy_id VARCHAR,
    keywords    VARCHAR[],
    source      VARCHAR DEFAULT 'manual',
    active      BOOLEAN DEFAULT TRUE,
    selected    BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS comments (
    comment_id  VARCHAR PRIMARY KEY,
    post_raw_id VARCHAR,
    platform    VARCHAR,
    text        VARCHAR,
    language    VARCHAR,
    author_hash VARCHAR,
    posted_at   TIMESTAMP,
    url         VARCHAR,
    keywords    VARCHAR[],
    simhash     UBIGINT,
    collected_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      VARCHAR PRIMARY KEY,
    job_type    VARCHAR,
    platform    VARCHAR,
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    status      VARCHAR,
    items       INTEGER DEFAULT 0,
    error       VARCHAR
);
