## Render - Salida a Empresa

### Resultado de auditoria local actual
- Falla critica 1: el entorno actual usa SQLite.
- Falla critica 2: el entorno actual usa `SECRET_KEY` de desarrollo.
- Advertencia: `SECURE_SSL_REDIRECT` esta desactivado.
- Advertencia: `FAIL_ON_SQLITE_IN_PRODUCTION` esta desactivado en este entorno local.
- Advertencia: falta `MAPBOX_TOKEN`.

### Para quedar listo en Render

#### Variables obligatorias
- Cargar los valores de [`.env.render.example`](/D:/Control_Trasporte/flota/.env.render.example).
- Reemplazar `SECRET_KEY`.
- Configurar `DATABASE_URL` con PostgreSQL real.
- Confirmar `FAIL_ON_SQLITE_IN_PRODUCTION=true`.

#### Deploy obligatorio
- Ejecutar `python manage.py migrate`.
- Ejecutar `python manage.py check`.
- Ejecutar `python manage.py auditar_preproduccion --allow-warnings`.
- Verificar `GET /healthz/`.

#### Limpieza obligatoria
- Programar `python manage.py limpiar_historicos`.
- Frecuencia recomendada: cada hora o cada 6 horas.

#### Backups obligatorios
- Activar backups del PostgreSQL en Render o proveedor asociado.
- Definir restauracion minima documentada.

### Prueba de salida antes de empresa
```bash
python manage.py poblar_escala --prefijo Render --empresas 5 --rutas 2 --puntos 8 --unidades 70 --historico-gps 0
python manage.py simular_gps --prefijo Render --empresas 5 --unidades 70 --iteraciones 120 --interval-seconds 5
```

### Criterio para decir "listo para empresa"
- Auditoria sin fallas criticas.
- `/healthz/` en `200`.
- PostgreSQL activo.
- Limpieza automatica activa.
- Backups activos.
- Prueba de carga en Render aprobada.
