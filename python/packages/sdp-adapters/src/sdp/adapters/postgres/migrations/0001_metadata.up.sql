-- 0001: kernel metadata, event log and evidence graph (§6, ADR-011).
CREATE TABLE hypotheses (
    hypothesis_id text NOT NULL,
    version integer NOT NULL CHECK (version >= 1),
    document jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (hypothesis_id, version)
);

CREATE TABLE predeclaration_drafts (
    predeclaration_id text PRIMARY KEY,
    document jsonb NOT NULL
);

CREATE TABLE frozen_predeclarations (
    predeclaration_hash text PRIMARY KEY CHECK (predeclaration_hash ~ '^sha256:[0-9a-f]{64}$'),
    document jsonb NOT NULL,
    ledger_entry_id text NOT NULL,
    frozen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE experiments (
    experiment_id text PRIMARY KEY,
    state text NOT NULL,
    protocol_name text NOT NULL,
    record jsonb NOT NULL,
    manifest jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX experiments_protocol ON experiments (protocol_name);

CREATE TABLE dossiers (
    dossier_id text PRIMARY KEY,
    experiment_id text NOT NULL REFERENCES experiments (experiment_id),
    document jsonb NOT NULL
);
CREATE INDEX dossiers_experiment ON dossiers (experiment_id);

CREATE TABLE datasets (
    artifact_id text PRIMARY KEY CHECK (artifact_id ~ '^sha256:[0-9a-f]{64}$'),
    kind text NOT NULL,
    idempotency_key text,
    parents text[] NOT NULL DEFAULT '{}',
    document jsonb NOT NULL
);
CREATE INDEX datasets_idempotency ON datasets (idempotency_key);
CREATE INDEX datasets_parents ON datasets USING gin (parents);

CREATE TABLE events (
    sequence bigserial PRIMARY KEY,
    name text NOT NULL,
    aggregate_id text NOT NULL,
    occurred_at timestamptz NOT NULL,
    document jsonb NOT NULL
);
CREATE INDEX events_aggregate ON events (aggregate_id, sequence);

-- Readers LISTEN on sdp_events; the payload is the new sequence number.
CREATE FUNCTION notify_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('sdp_events', NEW.sequence::text);
    RETURN NEW;
END;
$$;
CREATE TRIGGER events_notify AFTER INSERT ON events FOR EACH ROW EXECUTE FUNCTION notify_event();

-- Append-only: history in the event log is never rewritten.
CREATE FUNCTION refuse_event_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the event log is append-only';
END;
$$;
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events FOR EACH ROW EXECUTE FUNCTION refuse_event_change();

CREATE TABLE prov_nodes (
    node_id text PRIMARY KEY,
    node_type text NOT NULL CHECK (node_type IN ('entity', 'activity', 'agent')),
    attributes jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE prov_relations (
    relation text NOT NULL CHECK (relation IN ('used', 'wasGeneratedBy', 'wasAssociatedWith', 'wasDerivedFrom', 'wasAttributedTo')),
    subject text NOT NULL,
    object text NOT NULL,
    ordinal bigserial,
    PRIMARY KEY (relation, subject, object)
);
CREATE INDEX prov_relations_subject ON prov_relations (subject);
CREATE INDEX prov_relations_object ON prov_relations (object);
