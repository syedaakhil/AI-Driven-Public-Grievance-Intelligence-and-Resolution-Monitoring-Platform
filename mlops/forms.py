from django import forms
from django.utils.translation import gettext_lazy as _

from grievances.forms import style_fields
from grievances.models import Category


class NewsReviewForm(forms.Form):
    """A reviewer's decision on one news story the agent pre-filled."""
    APPROVE = 'approve'
    REJECT = 'reject'

    decision = forms.ChoiceField(choices=[(APPROVE, _('Approve')), (REJECT, _('Reject'))])
    category = forms.ModelChoiceField(label=_('Service category'), queryset=Category.objects.all(), required=False,
                                      empty_label=_('Choose a category'))
    district = forms.CharField(label=_('District'), max_length=60, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)

    def clean_district(self):
        return ' '.join(self.cleaned_data['district'].split())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('decision') == self.APPROVE and not cleaned.get('category'):
            self.add_error('category', _('Choose the service category before approving.'))
        return cleaned
