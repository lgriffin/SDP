-- 0004: acquisition plans (Integration v0.4.0 §5, ADR-020). A plan is stored as drafted; its
-- approval is recorded once and its execution once. The plan itself never changes, because its id
-- hashes it, and nothing is deleted.
CREATE TABLE acquisition_plans (
    plan_id text PRIMARY KEY CHECK (plan_id ~ '^sha256:[0-9a-f]{64}$'),
    profile_hash text NOT NULL CHECK (profile_hash ~ '^sha256:[0-9a-f]{64}$'),
    document jsonb NOT NULL,
    decision text CHECK (decision IN ('auto_approved', 'approved', 'refused')),
    set_id text REFERENCES investigation_sets (set_id),
    execution jsonb,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((set_id IS NULL) = (execution IS NULL)),
    CHECK (execution IS NULL OR decision IN ('auto_approved', 'approved'))
);

CREATE FUNCTION refuse_plan_rewrite() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'acquisition plans are retained (ADR-020)';
    END IF;
    IF NEW.plan_id <> OLD.plan_id OR NEW.profile_hash <> OLD.profile_hash
       OR (NEW.document - 'approval') <> (OLD.document - 'approval') THEN
        RAISE EXCEPTION 'a plan never changes; its id hashes it (ADR-020)';
    END IF;
    IF OLD.decision IS NOT NULL AND (NEW.decision IS DISTINCT FROM OLD.decision
       OR NEW.document <> OLD.document) THEN
        RAISE EXCEPTION 'a plan is decided once (INT-014)';
    END IF;
    IF OLD.execution IS NOT NULL AND (NEW.execution IS DISTINCT FROM OLD.execution
       OR NEW.set_id IS DISTINCT FROM OLD.set_id) THEN
        RAISE EXCEPTION 'a plan is executed once (INT-015)';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER acquisition_plans_append_only BEFORE UPDATE OR DELETE ON acquisition_plans
    FOR EACH ROW EXECUTE FUNCTION refuse_plan_rewrite();
