from django import forms

from .models import Cafe, Menu, OptionChoice, OptionGroup


class CafeForm(forms.ModelForm):
    class Meta:
        model = Cafe
        fields = ["name", "phone", "address"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "카페 이름"})}


class SellerLinkForm(forms.Form):
    account_code = forms.UUIDField(
        label="판매자 계정 연결 코드",
        widget=forms.TextInput(attrs={"placeholder": "판매자가 알려준 계정 연결 코드"}),
        error_messages={"invalid": "계정 연결 코드를 확인해 주세요."},
    )


class MenuForm(forms.ModelForm):
    class Meta:
        model = Menu
        fields = ["name", "category", "price", "is_active"]


class OptionGroupForm(forms.ModelForm):
    class Meta:
        model = OptionGroup
        fields = ["name", "mode", "required"]


class ChoiceFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        items = [
            f.cleaned_data
            for f in self.forms
            if f.cleaned_data and not f.cleaned_data.get("DELETE")
        ]
        if not items:
            raise forms.ValidationError("선택 항목을 하나 이상 등록해 주세요.")
        if len({item["name"] for item in items}) != len(items):
            raise forms.ValidationError(
                "같은 옵션 안에서 선택 항목의 이름이 겹치지 않게 해 주세요."
            )
        if (
            self.instance.mode == OptionGroup.Mode.SINGLE
            and sum(item.get("is_default", False) for item in items) > 1
        ):
            raise forms.ValidationError("하나 선택 옵션의 기본값은 하나만 지정할 수 있어요.")


ChoicesFormSet = forms.inlineformset_factory(
    OptionGroup,
    OptionChoice,
    fields=["name", "extra_price", "is_default"],
    formset=ChoiceFormSet,
    extra=3,
    can_delete=True,
    max_num=100,
    validate_max=True,
)
