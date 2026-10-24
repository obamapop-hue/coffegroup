import uuid

from django import forms


class MessageForm(forms.Form):
    body = forms.CharField(
        label="내용",
        max_length=3000,
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "궁금한 점을 적어 주세요."}),
    )
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)


class TicketForm(MessageForm):
    subject = forms.CharField(label="제목", max_length=100)
    field_order = ["subject", "body", "request_key"]
