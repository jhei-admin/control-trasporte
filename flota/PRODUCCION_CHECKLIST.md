## Checklist de Produccion

### Base de datos
- Usar PostgreSQL mediante `DATABASE_URL`.
- Activar `FAIL_ON_SQLITE_IN_PRODUCTION=true` en produccion.
- No operar varias empresas en produccion con `db.sqlite3`.

### Variables recomendadas
- `DEBUG=false`
- `DATABASE_URL=...`
- `SECRET_KEY=...`
- `SECURE_SSL_REDIRECT=true`
- `CSRF_COOKIE_SECURE=true`
- `SESSION_COOKIE_SECURE=true`
- `FAIL_ON_SQLITE_IN_PRODUCTION=true`
- `ALLOW_LEGACY_QR=false`

### Mantenimiento
- Ejecutar `python manage.py limpiar_historicos` al menos cada hora o cada 6 horas.
- Auditar residuos demo con `python manage.py purgar_demo`.
- Eliminar datos demo confirmados con `python manage.py purgar_demo --aplicar --empresa "Empresa Demo Transporte"` o usernames exactos cuando corresponda.
- Revisar `GET /healthz/` desde el proveedor o monitor externo.
- Mantener `GPS_RETENTION_DAYS`, `PARADAS_RETENTION_DAYS`, `INACTIVE_SESSION_RETENTION_DAYS` y `MENSAJES_RETENTION_DAYS` segun el volumen real.

### Despliegue
- Ejecutar `python manage.py migrate`.
- Ejecutar `python manage.py check`.
- Ejecutar `python manage.py test`.
- Confirmar que `/healthz/` responde `200`.

### Escala minima recomendada
- 2 a 5 empresas.
- 70 a 100 unidades por empresa.
- GPS con retencion corta y limpieza automatizada.
- PostgreSQL obligatorio para operacion sostenida.
