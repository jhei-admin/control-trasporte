## Backup y Recuperacion

### Objetivo
- Reducir el riesgo de perdida de informacion en operacion empresarial.
- Tener un procedimiento claro para restaurar el sistema si ocurre una falla.

### Alcance
- Base de datos PostgreSQL en Render.
- Codigo fuente en GitHub.
- Variables de entorno en Render.

### Minimo obligatorio antes de entrar a empresa
- Confirmar que PostgreSQL tenga respaldo activo.
- Confirmar quien es responsable de revisar los backups.
- Confirmar cada cuanto tiempo se genera el backup.
- Confirmar que existe un procedimiento de restauracion.
- Confirmar que las variables criticas estan registradas de forma segura fuera de Render.

### Que se debe respaldar

#### 1. Base de datos
- Empresas
- Vehiculos
- Rutas
- Puntos de control
- Registros de salida
- Sesiones
- GPS historico
- Mensajes globales

#### 2. Codigo
- Repositorio GitHub del proyecto.
- Rama principal estable.

#### 3. Configuracion
- Variables de entorno de produccion.
- Dominio principal y configuracion del despliegue.

### Frecuencia minima recomendada
- Base de datos: diaria como minimo.
- Codigo fuente: cada cambio ya queda respaldado en GitHub.
- Variables de entorno: cada vez que cambien valores criticos.

### Variables criticas que deben guardarse en lugar seguro
- `SECRET_KEY`
- `DATABASE_URL`
- `MAPBOX_TOKEN`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `CORS_ALLOWED_ORIGINS`

### Procedimiento de recuperacion

#### Escenario 1. Caida del servicio web
- Revisar logs en Render.
- Revisar `GET /healthz/`.
- Confirmar si el problema es aplicacion, migracion o base de datos.
- Hacer redeploy del ultimo commit estable si corresponde.

#### Escenario 2. Error de aplicacion despues de deploy
- Revisar logs del deploy.
- Revertir al ultimo commit estable en GitHub.
- Hacer nuevo deploy desde ese commit.
- Validar `login` y `healthz`.

#### Escenario 3. Corrupcion o perdida de datos
- Detener cambios operativos si la situacion lo requiere.
- Identificar la fecha y hora aproximada del incidente.
- Restaurar la base desde el backup mas reciente valido.
- Validar integridad minima:
- acceso al sistema
- empresas visibles
- vehiculos visibles
- panel despachador
- mapa
- `healthz`

### Validacion despues de restaurar
- `GET /healthz/` responde `200`.
- El login funciona.
- Se visualiza al menos una empresa correcta.
- Se visualizan vehiculos y rutas correctas.
- El panel despachador carga.
- No aparecen errores criticos en logs.

### Responsable operativo
- Definir una persona responsable del backup.
- Definir una persona responsable de restauracion.
- Si eres solo tu, dejarlo explicitamente asumido por el desarrollador responsable.

### Estado actual
- Codigo respaldado en GitHub.
- Despliegue validado en Render.
- PostgreSQL activo en Render.
- Falta confirmar politica real de backup del plan actual usado en Render.

### Conclusion
- El sistema no debe entrar a empresa sin un respaldo de base de datos confirmado.
- Si el backup no esta confirmado, la operacion sigue teniendo riesgo alto aunque la app funcione bien.
