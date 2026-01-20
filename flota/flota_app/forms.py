from django import forms
from .models import RegistroSalida

class RegistroSalidaForm(forms.ModelForm):
    class Meta:
        model = RegistroSalida
        fields = ['vehiculo', 'ruta']