from .models import OptionChoice, OptionGroup


def add_basic_options(menu):
    temperature = OptionGroup.objects.create(menu=menu, name="아이스 / 핫")
    shots = OptionGroup.objects.create(menu=menu, name="샷 추가")
    OptionChoice.objects.bulk_create(
        [
            OptionChoice(group=temperature, name="아이스", is_default=True),
            OptionChoice(group=temperature, name="핫"),
            OptionChoice(group=shots, name="샷 +0", is_default=True),
        ]
    )


def snapshot_menu(cafe):
    menus = cafe.menus.filter(is_active=True, is_available=True).prefetch_related(
        "option_groups__choices"
    )
    return [
        {
            "id": menu.pk,
            "name": menu.name,
            "category": menu.category,
            "price": menu.price,
            "groups": [
                {
                    "id": group.pk,
                    "name": group.name,
                    "mode": group.mode,
                    "required": group.required,
                    "choices": [
                        {
                            "id": choice.pk,
                            "name": choice.name,
                            "extra_price": choice.extra_price,
                            "is_default": choice.is_default,
                        }
                        for choice in group.choices.all()
                    ],
                }
                for group in menu.option_groups.all()
            ],
        }
        for menu in menus
    ]
