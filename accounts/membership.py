"""Small operator-only membership projection; never keeps Kakao IDs or login codes."""

from django.db import transaction

from .models import MembershipRecord, User


def sync_member(user):
    record, _ = MembershipRecord.objects.update_or_create(
        user=user,
        defaults={"nickname": user.display_name},
        create_defaults={"nickname": user.display_name, "joined_at": user.date_joined},
    )
    return record


@transaction.atomic
def ensure_existing_members():
    # Also cover members created by an older web revision during rolling deployment.
    members = User.objects.select_for_update(of=("self",), no_key=True, skip_locked=True).filter(
        membership_record__isnull=True
    )
    MembershipRecord.objects.bulk_create(
        [
            MembershipRecord(user=user, nickname=user.display_name, joined_at=user.date_joined)
            for user in members
        ],
        ignore_conflicts=True,
        batch_size=500,
    )
