"""
Fachada de compatibilidad para las vistas publicas de `flota_app`.

La implementacion historica quedo en `legacy_views.py` y la nueva
organizacion expone modulos por dominio desde `view_modules/`.
Esto permite ordenar imports y urls sin romper el proyecto.
"""

from .view_modules import *  # noqa: F401,F403
