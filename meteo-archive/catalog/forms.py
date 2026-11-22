from django import forms

from .models import Location, Station


class LocationFilter(forms.Form):
    place = forms.ModelChoiceField(
        label="Город или территория", queryset=Location.objects.all(), required=False, empty_label="Все места",
    )


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ("kind", "name", "region")


class StationForm(forms.ModelForm):
    class Meta:
        model = Station
        fields = ("location", "name", "profile", "latitude", "longitude")
