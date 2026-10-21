from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial")]
    operations = [
        migrations.RunSQL(
            """
            CREATE FUNCTION protect_room_snapshot() RETURNS trigger AS $$
            BEGIN
                IF NEW.menu_snapshot IS DISTINCT FROM OLD.menu_snapshot
                   OR NEW.cafe_snapshot IS DISTINCT FROM OLD.cafe_snapshot
                   OR NEW.cafe_id IS DISTINCT FROM OLD.cafe_id
                   OR NEW.token IS DISTINCT FROM OLD.token
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
                    RAISE EXCEPTION 'Order room snapshot is immutable'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER immutable_room_snapshot
                BEFORE UPDATE ON orders_room
                FOR EACH ROW EXECUTE FUNCTION protect_room_snapshot();
            """,
            """
            DROP TRIGGER IF EXISTS immutable_room_snapshot ON orders_room;
            DROP FUNCTION IF EXISTS protect_room_snapshot();
            """,
        ),
    ]
