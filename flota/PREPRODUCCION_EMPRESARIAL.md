## Preproduccion Empresarial

### Objetivo
- Dejar el sistema listo para entrar a una empresa con riesgo bajo y controlado.
- Validar no solo la logica local, sino tambien la operacion real en Render.

### Estado actual del proyecto
- La aplicacion ya soporta PostgreSQL.
- Existe `healthz/` para verificar base de datos y migraciones.
- Existe limpieza automatizable con `python manage.py limpiar_historicos`.
- La validacion local aprobo hasta 5 empresas x 70 unidades.

### Fase 1 obligatoria

#### 1. Render + PostgreSQL
- Confirmar que `DATABASE_URL` apunta a PostgreSQL real en Render.
- Confirmar que `DEBUG=false`.
- Confirmar que `SECRET_KEY` no es la de desarrollo.
- Confirmar que `FAIL_ON_SQLITE_IN_PRODUCTION=true`.
- Ejecutar `python manage.py auditar_preproduccion --allow-warnings`.

Estado actual:
- Aprobada en Render el 4 de abril de 2026.
- Auditoria automatica: `0 fallas`, `0 advertencias`, `11 ok`.
- `healthz`: `200`.

#### 2. Health check
- Verificar `GET /healthz/` con respuesta `200`.
- Confirmar que `pending_migrations=0`.
- Revisar el endpoint despues de cada deploy.

#### 3. Limpieza automatica
- Programar `python manage.py limpiar_historicos` al menos cada hora o cada 6 horas.
- Revisar retencion real:
- `GPS_RETENTION_DAYS`
- `PARADAS_RETENTION_DAYS`
- `INACTIVE_SESSION_RETENTION_DAYS`
- `MENSAJES_RETENTION_DAYS`

#### 4. Backups
- Activar backups del PostgreSQL administrado por Render o por el proveedor.
- Definir frecuencia minima diaria.
- Tener un punto de restauracion comprobable.
- Guardar responsable, frecuencia y procedimiento de restauracion.

### Fase 2 obligatoria

#### 1. Monitoreo
- Revisar logs de aplicación en cada prueba de carga.
- Vigilar tiempo de respuesta, reinicios, CPU, memoria y errores 5xx.
- Monitorear `healthz/` desde fuera del servidor.

#### 2. Alertas
- Configurar alerta por caida del `healthz/`.
- Configurar alerta por errores repetidos en API GPS, heartbeat y login.
- Configurar alerta por reinicios del servicio o caidas de base de datos.

#### 3. Prueba de carga en Render
- Sembrar datos demo con `python manage.py poblar_escala`.
- Ejecutar simulacion GPS contra el entorno real.
- Correr prueba de 30 a 60 minutos.
- Probar varios despachadores conectados al mismo tiempo.

### Fase 3 final
- Hacer piloto con 1 empresa real.
- Empezar con pocas unidades y seguimiento cercano.
- Revisar logs y `healthz/` durante el piloto.
- Ajustar retencion, limpieza o capacidad segun resultados.

### Comandos utiles
```bash
python manage.py auditar_preproduccion --allow-warnings
python manage.py check
python manage.py test
python manage.py limpiar_historicos --dry-run
python manage.py poblar_escala --empresas 5 --unidades 70 --rutas 2 --puntos 8 --historico-gps 0
python manage.py simular_gps --prefijo Stress --empresas 5 --unidades 70 --iteraciones 120
```

### Criterio de salida
- Fase 1 completa.
- Fase 2 completa.
- Auditoria sin fallas criticas.
- Prueba en Render aprobada sin errores graves.
- Backups confirmados y restauracion definida.

### Nota importante
- La prueba local valida logica y capacidad inicial.
- La prueba en Render valida el comportamiento real de operacion.
- La empresa no necesita prestar su operacion para estas pruebas.
