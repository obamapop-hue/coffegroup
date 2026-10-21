"""Validated, atomic spreadsheet edits. Existing room snapshots are never touched."""

import hashlib
import json
from collections import Counter, defaultdict
from uuid import UUID

from django import forms
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .models import Cafe, CafeAccessEvent, ManagementChange, Menu, OptionChoice, OptionGroup


class SheetError(Exception):
    def __init__(self, errors, status=400):
        self.errors = errors
        self.status = status


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def require_operator(actor):
    if not actor.is_authenticated or not actor.is_active or not actor.is_operator:
        raise PermissionDenied


def history(cafe=None):
    return [
        {
            "time": timezone.localtime(change.created_at).strftime("%Y.%m.%d %H:%M"),
            "summary": change.summary,
        }
        for change in ManagementChange.objects.filter(cafe=cafe)[:10]
    ]


def cafe_state():
    rows = [
        {
            "key": f"cafe-{cafe.pk}",
            "id": cafe.pk,
            "name": cafe.name,
            "phone": cafe.phone,
            "address": cafe.address,
            "seller_code": str(cafe.seller.account_code) if cafe.seller_id else "",
            "seller_name": cafe.seller.display_name if cafe.seller_id else "",
            "is_open": cafe.is_open,
            "manage_url": reverse("cafes:manage", args=[cafe.pk]),
        }
        for cafe in Cafe.objects.select_related("seller").order_by("pk")
    ]
    version = digest(
        [{k: row[k] for k in ("id", "name", "phone", "address", "seller_code")} for row in rows]
    )
    return {"kind": "cafes", "version": version, "rows": rows, "history": history()}


def catalog_state(cafe):
    menus, options = [], []
    for menu in cafe.menus.order_by("pk").prefetch_related("option_groups__choices"):
        summaries = []
        for group in menu.option_groups.all():
            choices = list(group.choices.all())
            defaults = [choice.name for choice in choices if choice.is_default]
            summaries.append(f"{group.name}: {', '.join(defaults) if defaults else '기본값 없음'}")
            for choice in choices:
                options.append(
                    {
                        "key": f"choice-{choice.pk}",
                        "id": choice.pk,
                        "menu_key": f"menu-{menu.pk}",
                        "group_key": f"group-{group.pk}",
                        "group_name": group.name,
                        "mode": group.mode,
                        "required": group.required,
                        "name": choice.name,
                        "extra_price": choice.extra_price,
                        "is_default": choice.is_default,
                    }
                )
        menus.append(
            {
                "key": f"menu-{menu.pk}",
                "id": menu.pk,
                "name": menu.name,
                "category": menu.category,
                "price": menu.price,
                "is_active": menu.is_active,
                "is_available": menu.is_available,
                "option_summary": " · ".join(summaries),
            }
        )
    editable_menus = [
        {k: row[k] for k in ("id", "name", "category", "price", "is_active")} for row in menus
    ]
    groups = list(
        OptionGroup.objects.filter(menu__cafe=cafe)
        .order_by("pk")
        .values("id", "menu_id", "name", "mode", "required")
    )
    return {
        "kind": "catalog",
        "version": digest([editable_menus, groups, options]),
        "menus": menus,
        "options": options,
        "history": history(cafe),
    }


def fail(field, message, key="", sheet="", status=400):
    raise SheetError([{"key": key, "field": field, "message": message, "sheet": sheet}], status)


def validate_version(data, current):
    if not isinstance(data, dict):
        fail("", "저장할 내용을 확인해 주세요.")
    if data.get("version") != current["version"]:
        fail(
            "",
            "다른 창에서 내용이 변경됐어요. 입력 내용은 유지됩니다. "
            "최신 내용을 확인한 뒤 다시 저장해 주세요.",
            status=409,
        )


def rows_from(data, field, current):
    rows = data.get(field)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        fail("", "표 데이터를 확인해 주세요.", sheet=field)
    keys, ids = set(), set()
    known = {row["id"] for row in current}
    for row in rows:
        key, pk = row.get("key"), row.get("id")
        if not isinstance(key, str) or not key or len(key) > 100 or key in keys:
            fail("", "행 식별 정보가 겹치거나 올바르지 않아요.", sheet=field)
        keys.add(key)
        if pk is not None:
            if type(pk) is not int or pk not in known or pk in ids:
                fail("", "다른 표의 항목을 변경할 수 없어요.", key, field)
            ids.add(pk)
        if type(row.get("_delete", False)) is not bool:
            fail("", "삭제 표시를 확인해 주세요.", key, field)
    if ids != known:
        fail("", "기존 항목이 누락됐어요. 최신 표를 다시 불러와 주세요.", sheet=field)
    return rows


def text(value, maximum, required=True):
    return forms.CharField(max_length=maximum, required=required).clean(value)


def boolean(value):
    if type(value) is bool:
        return value
    raise forms.ValidationError("체크 여부를 확인해 주세요.")


def price(value):
    if isinstance(value, str):
        value = value.strip().replace(",", "").removeprefix("₩").removesuffix("원").strip()
    if type(value) is bool:
        raise forms.ValidationError("0 이상의 정수 금액을 입력해 주세요.")
    return forms.IntegerField(min_value=0, max_value=2147483647).clean(value)


def clean_row(row, rules, errors, sheet):
    cleaned = {"key": row["key"], "id": row.get("id"), "_delete": row.get("_delete", False)}
    for field, cleaner in rules.items():
        try:
            cleaned[field] = cleaner(row.get(field))
        except forms.ValidationError as error:
            errors.append(
                {"key": row["key"], "field": field, "message": error.messages[0], "sheet": sheet}
            )
    return cleaned


def seller_code(value):
    value = text(value, 36, False)
    if not value:
        return ""
    try:
        return str(UUID(value))
    except ValueError as error:
        raise forms.ValidationError("계정 연결 코드 형식을 확인해 주세요.") from error


@transaction.atomic
def save_cafes(*, actor, data):
    require_operator(actor)
    # Serializes empty-table inserts and multi-cafe seller swaps.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [1281737481])
    current = cafe_state()
    validate_version(data, current)
    rows = rows_from(data, "rows", current["rows"])
    errors, cleaned = [], []
    rules = {
        "name": lambda v: text(v, 80),
        "phone": lambda v: text(v, 30, False),
        "address": lambda v: text(v, 250, False),
        "seller_code": seller_code,
    }
    for row in rows:
        if row.get("_delete"):
            fail(
                "",
                "카페 기록은 삭제하지 않아요. 판매자 연결 해제는 연결코드를 비워 주세요.",
                row["key"],
                "rows",
            )
        cleaned.append(clean_row(row, rules, errors, "rows"))
    if errors:
        raise SheetError(errors)
    codes = {row["seller_code"] for row in cleaned if row["seller_code"]}
    sellers = {
        str(user.account_code): user
        for user in User.objects.select_for_update(no_key=True)
        .filter(account_code__in=codes, is_active=True)
        .order_by("pk")
    }
    counts = Counter(row["seller_code"] for row in cleaned if row["seller_code"])
    for row in cleaned:
        code = row["seller_code"]
        message = (
            "가입한 계정의 연결코드를 확인해 주세요."
            if code and code not in sellers
            else "한 계정은 한 카페에만 연결할 수 있어요."
            if counts[code] > 1
            else ""
        )
        if message:
            errors.append(
                {"key": row["key"], "field": "seller_code", "message": message, "sheet": "rows"}
            )
    cafes = {cafe.pk: cafe for cafe in Cafe.objects.select_for_update().order_by("pk")}
    validate_version(data, cafe_state())
    if errors:
        raise SheetError(errors)
    previous = {row["id"]: row for row in current["rows"]}
    created = updated = linked = 0
    # Release changed assignments before assigning any targets, allowing swaps.
    for row in cleaned:
        if row["id"] is not None:
            cafe = cafes[row["id"]]
            target = sellers.get(row["seller_code"])
            if cafe.seller_id and cafe.seller_id != (target.pk if target else None):
                cafe.seller = None
                cafe.is_open = False
                cafe.save(update_fields=["seller", "is_open"])
                CafeAccessEvent.objects.create(cafe=cafe, actor=actor, action="unlinked")
    for row in cleaned:
        cafe = cafes.get(row["id"])
        if cafe is None:
            cafe = Cafe.objects.create(name=row["name"], phone=row["phone"], address=row["address"])
            created += 1
        else:
            if any(row[field] != previous[cafe.pk][field] for field in rules):
                updated += 1
            cafe.name, cafe.phone, cafe.address = row["name"], row["phone"], row["address"]
            cafe.save(update_fields=["name", "phone", "address"])
        target = sellers.get(row["seller_code"])
        if cafe.seller_id != (target.pk if target else None):
            cafe.seller = target
            cafe.is_open = False
            cafe.save(update_fields=["seller", "is_open"])
            if target:
                CafeAccessEvent.objects.create(cafe=cafe, actor=actor, action="linked")
                linked += 1
    if created or updated or linked:
        ManagementChange.objects.create(
            actor=actor,
            summary=f"카페 {created}개 등록 · {updated}개 수정 · 판매자 {linked}개 연결",
        )
    return cafe_state()


@transaction.atomic
def save_catalog(*, actor, cafe_id, data):
    require_operator(actor)
    cafe = Cafe.objects.select_for_update().get(pk=cafe_id)
    current = catalog_state(cafe)
    validate_version(data, current)
    menus_raw = rows_from(data, "menus", current["menus"])
    options_raw = rows_from(data, "options", current["options"])
    errors, menus, options = [], [], []
    menu_rules = {
        "name": lambda v: text(v, 80),
        "category": lambda v: text(v, 40, False),
        "price": price,
        "is_active": boolean,
    }
    option_rules = {
        "menu_key": lambda v: text(v, 100),
        "group_key": lambda v: text(v, 100),
        "group_name": lambda v: text(v, 60),
        "mode": forms.ChoiceField(choices=OptionGroup.Mode.choices).clean,
        "required": boolean,
        "name": lambda v: text(v, 60),
        "extra_price": price,
        "is_default": boolean,
    }
    for row in menus_raw:
        if row.get("_delete") and row.get("id"):
            original = next(item for item in current["menus"] if item["id"] == row["id"])
            row = {**original, "key": row["key"], "_delete": True}
        menus.append(clean_row(row, menu_rules, errors, "menus"))
    menu_map = {row["key"]: row for row in menus}
    deleted_menus = {row["key"] for row in menus if row["_delete"]}
    for row in options_raw:
        if row.get("_delete") or row.get("menu_key") in deleted_menus:
            continue
        options.append(clean_row(row, option_rules, errors, "options"))
    if errors:
        raise SheetError(errors)
    existing_groups = {
        f"group-{group.pk}": group for group in OptionGroup.objects.filter(menu__cafe=cafe)
    }
    groups = defaultdict(list)
    for row in options:
        key, menu_key = row["group_key"], row["menu_key"]
        if menu_key not in menu_map:
            errors.append(
                {
                    "key": row["key"],
                    "field": "menu_key",
                    "message": "메뉴를 선택해 주세요.",
                    "sheet": "options",
                }
            )
            continue
        if key.startswith("group-") and (
            key not in existing_groups or existing_groups[key].menu_id != menu_map[menu_key]["id"]
        ):
            errors.append(
                {
                    "key": row["key"],
                    "field": "group_name",
                    "message": "다른 메뉴의 옵션을 변경할 수 없어요.",
                    "sheet": "options",
                }
            )
        groups[key].append(row)
    names = {}
    for key, items in groups.items():
        first = items[0]
        identity = (first["menu_key"], first["group_name"])
        for row in items:
            field, message = "", ""
            if any(
                row[field] != first[field]
                for field in ("menu_key", "group_name", "mode", "required")
            ):
                field, message = (
                    "group_name",
                    "같은 옵션 그룹의 이름·선택 방식·필수 여부를 맞춰 주세요.",
                )
            elif identity in names and names[identity] != key:
                field, message = "group_name", "같은 메뉴에 이름이 같은 옵션 그룹이 겹쳤어요."
            elif Counter(item["name"] for item in items)[row["name"]] > 1:
                field, message = "name", "같은 옵션 그룹에서 선택 항목 이름이 겹쳐요."
            elif first["mode"] == "single" and sum(item["is_default"] for item in items) > 1:
                field, message = "is_default", "하나 선택 옵션의 기본값은 하나만 지정해 주세요."
            if message:
                errors.append(
                    {"key": row["key"], "field": field, "message": message, "sheet": "options"}
                )
        names[identity] = key
    if errors:
        raise SheetError(errors)
    previous_menus = {row["id"]: row for row in current["menus"]}
    old_choices = {
        choice.pk: choice for choice in OptionChoice.objects.filter(group__menu__cafe=cafe)
    }
    menu_objects = {menu.pk: menu for menu in cafe.menus.all()}
    created = updated = hidden = 0
    for row in menus:
        menu = menu_objects.get(row["id"])
        if row["_delete"]:
            if menu and menu.is_active:
                menu.is_active = False
                menu.save(update_fields=["is_active"])
                hidden += 1
            if menu:
                menu_map[row["key"]]["object"] = menu
            continue
        values = {field: row[field] for field in menu_rules}
        if menu is None:
            menu = Menu.objects.create(cafe=cafe, **values)
            created += 1
        else:
            if any(previous_menus[menu.pk][field] != row[field] for field in menu_rules):
                updated += 1
            for field, value in values.items():
                setattr(menu, field, value)
            menu.save(update_fields=list(values))
        menu_map[row["key"]]["object"] = menu
    kept_groups, kept_choices = set(), set()
    for key, items in groups.items():
        first = items[0]
        group = existing_groups.get(key)
        values = {
            "menu": menu_map[first["menu_key"]]["object"],
            "name": first["group_name"],
            "mode": first["mode"],
            "required": first["required"],
        }
        if group is None:
            group = OptionGroup.objects.create(**values)
        else:
            for field, value in values.items():
                setattr(group, field, value)
            group.save()
        kept_groups.add(group.pk)
        for row in items:
            choice = old_choices.get(row["id"])
            values = {
                "group": group,
                "name": row["name"],
                "extra_price": row["extra_price"],
                "is_default": row["is_default"],
            }
            if choice is None:
                choice = OptionChoice.objects.create(**values)
            else:
                for field, value in values.items():
                    setattr(choice, field, value)
                choice.save()
            kept_choices.add(choice.pk)
    # Soft-removed menus keep their source options too; room snapshots are separate.
    hidden_ids = [row["id"] for row in menus if row["_delete"] and row["id"]]
    OptionChoice.objects.filter(group__menu__cafe=cafe).exclude(
        group__menu_id__in=hidden_ids
    ).exclude(pk__in=kept_choices).delete()
    OptionGroup.objects.filter(menu__cafe=cafe).exclude(menu_id__in=hidden_ids).exclude(
        pk__in=kept_groups
    ).delete()
    result = catalog_state(cafe)
    result["menu_keys"] = {
        row["key"]: f"menu-{row['object'].pk}" for row in menu_map.values() if "object" in row
    }
    if result["version"] != current["version"]:
        ManagementChange.objects.create(
            actor=actor,
            cafe=cafe,
            summary=f"메뉴 {created}개 추가 · {updated}개 수정 · {hidden}개 숨김 · 옵션 표 저장",
        )
        result["history"] = history(cafe)
    return result
