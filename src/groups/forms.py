from django import forms

from groups.models import Group


class GroupBannerForm(forms.ModelForm):
    """Upload a group's cover image; the model field validates size and format."""

    class Meta:
        """Only the banner."""

        model = Group
        fields = ["banner"]
