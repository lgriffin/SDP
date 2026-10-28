-- 0002: Investigate (UC-03): investigation sets and investigations, kept forever (INV-010).
CREATE TABLE investigation_sets (
    set_id text PRIMARY KEY,
    field_id text NOT NULL,
    profile_hash text NOT NULL CHECK (profile_hash ~ '^sha256:[0-9a-f]{64}$'),
    document jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE investigations (
    investigation_id text PRIMARY KEY,
    set_id text NOT NULL REFERENCES investigation_sets (set_id),
    state text NOT NULL CHECK (state IN ('open', 'parked', 'promoted', 'closed')),
    document jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX investigations_set ON investigations (set_id);

-- Parked and closed investigations keep their dossiers and reasons; nothing is deleted.
CREATE FUNCTION refuse_investigation_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'investigations and their sets are retained (INV-010)';
END;
$$;
CREATE TRIGGER investigations_retained BEFORE DELETE ON investigations
    FOR EACH ROW EXECUTE FUNCTION refuse_investigation_delete();
CREATE TRIGGER investigation_sets_retained BEFORE DELETE ON investigation_sets
    FOR EACH ROW EXECUTE FUNCTION refuse_investigation_delete();
