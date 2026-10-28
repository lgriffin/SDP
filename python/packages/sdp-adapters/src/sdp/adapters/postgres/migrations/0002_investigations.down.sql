DROP TRIGGER IF EXISTS investigation_sets_retained ON investigation_sets;
DROP TRIGGER IF EXISTS investigations_retained ON investigations;
DROP FUNCTION IF EXISTS refuse_investigation_delete();
DROP TABLE IF EXISTS investigations;
DROP TABLE IF EXISTS investigation_sets;
