-- 0003: capability descriptors (Integration v0.4.0 §2, ADR-017). A descriptor is replaced, never
-- edited in place: put supersedes the current row and inserts a new one, so every past descriptor
-- (and the date it was verified) stays readable for the plans that recorded it.
CREATE TABLE capability_descriptors (
    descriptor_id bigserial PRIMARY KEY,
    source_id text NOT NULL,
    verified_on date NOT NULL,
    document jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    superseded_at timestamptz
);
CREATE UNIQUE INDEX capability_descriptors_current ON capability_descriptors (source_id) WHERE superseded_at IS NULL;

CREATE FUNCTION refuse_descriptor_rewrite() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'capability descriptors are retained; put a newer one instead (INT-001)';
    END IF;
    IF NEW.source_id <> OLD.source_id OR NEW.verified_on <> OLD.verified_on OR NEW.document <> OLD.document
       OR NEW.recorded_at <> OLD.recorded_at OR OLD.superseded_at IS NOT NULL THEN
        RAISE EXCEPTION 'capability descriptors are never edited in place; only superseded (INT-001)';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER capability_descriptors_append_only BEFORE UPDATE OR DELETE ON capability_descriptors
    FOR EACH ROW EXECUTE FUNCTION refuse_descriptor_rewrite();
