from importlib import import_module

from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION protect_sent_room() RETURNS trigger AS $$
DECLARE has_final boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IN ('pending', 'accepted', 'completed', 'rejected', 'cancelled') THEN
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
    IF NOT has_final
       AND NEW.status IN ('pending', 'accepted', 'completed', 'rejected', 'cancelled') THEN
        RAISE EXCEPTION 'Sent state requires a final order' USING ERRCODE = '23514';
    END IF;
    IF has_final AND NEW.organizer_id IS DISTINCT FROM OLD.organizer_id
       AND NEW.organizer_id IS NOT NULL THEN
        RAISE EXCEPTION 'Sent room owner cannot change' USING ERRCODE = '23514';
    END IF;
    IF has_final AND NOT (
        (OLD.status = 'closed' AND NEW.status = 'pending')
        OR (OLD.status = 'pending' AND NEW.status IN ('accepted', 'rejected', 'cancelled'))
        OR (OLD.status = 'accepted' AND NEW.status = 'completed'
            AND NEW.resolved_at IS NOT DISTINCT FROM OLD.resolved_at
            AND NEW.rejection_reason IS NOT DISTINCT FROM OLD.rejection_reason)
        OR (OLD.status = NEW.status AND NEW.resolved_at IS NOT DISTINCT FROM OLD.resolved_at
            AND NEW.rejection_reason IS NOT DISTINCT FROM OLD.rejection_reason)
    ) THEN
        RAISE EXCEPTION 'Sent state cannot be reset or rewritten' USING ERRCODE = '23514';
    END IF;
    IF NEW.ready_at IS NOT NULL AND (
        date_trunc('minute', NEW.ready_at) IS DISTINCT FROM NEW.ready_at
        OR mod(extract(minute from NEW.ready_at)::int, 5) <> 0
    ) THEN
        RAISE EXCEPTION 'Ready time requires a five-minute boundary' USING ERRCODE = '23514';
    END IF;
    IF has_final AND NEW.ready_at IS DISTINCT FROM OLD.ready_at
       AND NOT (OLD.status = 'pending' AND NEW.status = 'accepted') THEN
        RAISE EXCEPTION 'Accepted ready time is immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.completed_at IS DISTINCT FROM OLD.completed_at
       AND NOT (OLD.status = 'accepted' AND NEW.status = 'completed') THEN
        RAISE EXCEPTION 'Completion time is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    dependencies = [("orders", "0008_order_completion")]
    operations = [
        migrations.RunSQL(SQL, import_module("orders.migrations.0007_protect_ready_time").SQL)
    ]
