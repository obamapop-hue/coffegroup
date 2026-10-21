from django.db import migrations

SQL = """
CREATE FUNCTION protect_final_order() RETURNS trigger AS $$
DECLARE room_state text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Final order is immutable' USING ERRCODE = '23514';
    END IF;
    SELECT status INTO room_state FROM orders_room WHERE id = NEW.room_id FOR UPDATE;
    IF room_state IS DISTINCT FROM 'closed' THEN
        RAISE EXCEPTION 'Final order requires a closed room' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_final_order BEFORE INSERT OR UPDATE OR DELETE ON orders_finalorder
    FOR EACH ROW EXECUTE FUNCTION protect_final_order();

CREATE FUNCTION protect_sent_submission() RETURNS trigger AS $$
DECLARE target_room bigint;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.room_id IS DISTINCT FROM OLD.room_id THEN
        RAISE EXCEPTION 'Submission cannot move rooms' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN target_room := OLD.room_id;
    ELSE target_room := NEW.room_id; END IF;
    PERFORM id FROM orders_room WHERE id = target_room FOR UPDATE;
    IF EXISTS (SELECT 1 FROM orders_finalorder WHERE room_id = target_room) THEN
        RAISE EXCEPTION 'Sent submissions are immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_sent_submission BEFORE INSERT OR UPDATE OR DELETE ON orders_submission
    FOR EACH ROW EXECUTE FUNCTION protect_sent_submission();

CREATE FUNCTION protect_sent_revision() RETURNS trigger AS $$
DECLARE target_submission bigint; target_room bigint;
BEGIN
    -- Account erasure may unlink an audit actor without altering order content.
    IF TG_OP = 'UPDATE' AND NEW.actor_id IS NULL AND OLD.actor_id IS NOT NULL
       AND (to_jsonb(NEW) - 'actor_id') IS NOT DISTINCT FROM (to_jsonb(OLD) - 'actor_id') THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.submission_id IS DISTINCT FROM OLD.submission_id THEN
        RAISE EXCEPTION 'Revision cannot move submissions' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN target_submission := OLD.submission_id;
    ELSE target_submission := NEW.submission_id; END IF;
    SELECT room_id INTO target_room FROM orders_submission WHERE id = target_submission;
    PERFORM id FROM orders_room WHERE id = target_room FOR UPDATE;
    IF EXISTS (SELECT 1 FROM orders_finalorder WHERE room_id = target_room) THEN
        RAISE EXCEPTION 'Sent revisions are immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_sent_revision BEFORE INSERT OR UPDATE OR DELETE
    ON orders_submissionrevision
    FOR EACH ROW EXECUTE FUNCTION protect_sent_revision();

CREATE FUNCTION protect_sent_room() RETURNS trigger AS $$
DECLARE has_final boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IN ('pending', 'accepted', 'rejected', 'cancelled') THEN
            RAISE EXCEPTION 'Sent state requires a final order' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT EXISTS (SELECT 1 FROM orders_finalorder WHERE room_id = OLD.id) INTO has_final;
    IF TG_OP = 'DELETE' THEN
        IF has_final THEN
            RAISE EXCEPTION 'Sent room cannot be deleted' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;
    IF NOT has_final AND NEW.status IN ('pending', 'accepted', 'rejected', 'cancelled') THEN
        RAISE EXCEPTION 'Sent state requires a final order' USING ERRCODE = '23514';
    END IF;
    IF has_final AND NEW.organizer_id IS DISTINCT FROM OLD.organizer_id
       AND NEW.organizer_id IS NOT NULL THEN
        RAISE EXCEPTION 'Sent room owner cannot change' USING ERRCODE = '23514';
    END IF;
    IF has_final AND NOT (
        (OLD.status = 'closed' AND NEW.status = 'pending')
        OR (OLD.status = 'pending' AND NEW.status IN ('accepted', 'rejected', 'cancelled'))
        OR (OLD.status = NEW.status AND NEW.resolved_at IS NOT DISTINCT FROM OLD.resolved_at
            AND NEW.rejection_reason IS NOT DISTINCT FROM OLD.rejection_reason)
    ) THEN
        RAISE EXCEPTION 'Sent state cannot be reset or rewritten' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_sent_room BEFORE INSERT OR UPDATE OR DELETE ON orders_room
    FOR EACH ROW EXECUTE FUNCTION protect_sent_room();

CREATE FUNCTION protect_sent_event() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.room_id IS DISTINCT FROM OLD.room_id THEN
        RAISE EXCEPTION 'Event cannot move rooms' USING ERRCODE = '23514';
    END IF;
    PERFORM id FROM orders_room WHERE id = OLD.room_id FOR UPDATE;
    IF EXISTS (SELECT 1 FROM orders_finalorder WHERE room_id = OLD.room_id) THEN
        RAISE EXCEPTION 'Sent history is immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_sent_event BEFORE UPDATE OR DELETE ON orders_roomevent
    FOR EACH ROW EXECUTE FUNCTION protect_sent_event();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS immutable_sent_event ON orders_roomevent;
DROP FUNCTION IF EXISTS protect_sent_event();
DROP TRIGGER IF EXISTS immutable_sent_room ON orders_room;
DROP FUNCTION IF EXISTS protect_sent_room();
DROP TRIGGER IF EXISTS immutable_sent_revision ON orders_submissionrevision;
DROP FUNCTION IF EXISTS protect_sent_revision();
DROP TRIGGER IF EXISTS immutable_sent_submission ON orders_submission;
DROP FUNCTION IF EXISTS protect_sent_submission();
DROP TRIGGER IF EXISTS immutable_final_order ON orders_finalorder;
DROP FUNCTION IF EXISTS protect_final_order();
"""


class Migration(migrations.Migration):
    dependencies = [("orders", "0004_final_order")]
    operations = [migrations.RunSQL(SQL, REVERSE_SQL)]
