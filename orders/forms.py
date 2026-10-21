import uuid

from django import forms
from django.core import signing

SIGNING_SALT = "orders.collection.request"


def submission_token(room_id):
    return signing.dumps({"room": room_id, "key": str(uuid.uuid4())}, salt=SIGNING_SALT)


class OrderForm(forms.Form):
    nickname = forms.CharField(label="이름(닉네임)", max_length=30, strip=True)
    items = forms.JSONField(widget=forms.HiddenInput)
    request_token = forms.CharField(widget=forms.HiddenInput, required=False)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False, min_value=1)

    def __init__(self, *args, room_id, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.room_id = room_id
        self.editing = editing
        self.fields["nickname"].widget.attrs.update(
            {"autocomplete": "off", "placeholder": "총무가 알아볼 수 있는 이름", "maxlength": 30}
        )

    def clean(self):
        data = super().clean()
        if self.editing:
            if not data.get("version"):
                raise forms.ValidationError("수정 화면을 다시 열어 주세요.")
        else:
            try:
                token = signing.loads(
                    data.get("request_token", ""), salt=SIGNING_SALT, max_age=1800
                )
                if token["room"] != self.room_id:
                    raise ValueError
                data["request_key"] = uuid.UUID(token["key"])
            except (signing.BadSignature, ValueError, TypeError, KeyError):
                raise forms.ValidationError("화면을 새로 열어 다시 제출해 주세요.") from None
        return data


class SellerAcceptForm(forms.Form):
    ready_at = forms.DateTimeField(label="완료 예정시간", widget=forms.Select)

    def __init__(self, *args, choices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ready_at"].widget.choices = [("", "완료시간 선택"), *choices]


class SellerRejectForm(forms.Form):
    reason = forms.CharField(
        label="거절 사유",
        max_length=30,
        strip=True,
        widget=forms.TextInput(
            attrs={"placeholder": "거절 사유 (30자 이내)", "autocomplete": "off"}
        ),
    )
