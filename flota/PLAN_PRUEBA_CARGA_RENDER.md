## Plan de Prueba de Carga en Render

### Objetivo
- Validar en servidor real la misma carga que ya fue aprobada en local.
- Confirmar que Render soporta operacion multiempresa con GPS simulado.

### Alcance
- Entorno Render con PostgreSQL.
- Datos simulados.
- Sin usar operacion real de una empresa.

### Requisito previo
- Tener Render con Shell, one-off jobs o una forma equivalente de ejecutar comandos.
- Tener Fase 1 de preproduccion aprobada.
- Tener backup confirmado antes de sembrar carga de prueba.

### Pruebas objetivo

#### Nivel 1
- 2 empresas x 70 unidades sin historico.
- 2 empresas x 70 unidades con historico.

#### Nivel 2
- 5 empresas x 70 unidades sin historico.
- 5 empresas x 70 unidades con historico.

#### Nivel 3 recomendado
- 10 empresas x 70 unidades.
- varios despachadores conectados al mismo tiempo.
- prueba prolongada de 30 a 60 minutos con `--sleep-real`.

### Preparacion
- Confirmar `healthz` en `200`.
- Confirmar que no haya migraciones pendientes.
- Confirmar que PostgreSQL este operativo.
- Confirmar que el entorno usado sea de pruebas o una ventana controlada.

### Comandos base

#### Sembrar 2 empresas x 70 unidades
```bash
python manage.py poblar_escala --prefijo Render2 --empresas 2 --rutas 2 --puntos 8 --unidades 70 --historico-gps 0
```

#### Simular 2 empresas x 70 unidades sin historico
```bash
python manage.py simular_gps --prefijo Render2 --empresas 2 --unidades 70 --iteraciones 120 --interval-seconds 5 --sin-historico
```

#### Simular 2 empresas x 70 unidades con historico
```bash
python manage.py simular_gps --prefijo Render2 --empresas 2 --unidades 70 --iteraciones 120 --interval-seconds 5
```

#### Sembrar 5 empresas x 70 unidades
```bash
python manage.py poblar_escala --prefijo Render5 --empresas 5 --rutas 2 --puntos 8 --unidades 70 --historico-gps 0
```

#### Simular 5 empresas x 70 unidades sin historico
```bash
python manage.py simular_gps --prefijo Render5 --empresas 5 --unidades 70 --iteraciones 120 --interval-seconds 5 --sin-historico
```

#### Simular 5 empresas x 70 unidades con historico
```bash
python manage.py simular_gps --prefijo Render5 --empresas 5 --unidades 70 --iteraciones 120 --interval-seconds 5
```

### Prueba prolongada recomendada
```bash
python manage.py simular_gps --prefijo Render5 --empresas 5 --unidades 70 --iteraciones 360 --interval-seconds 5 --sleep-real
```

### Que validar durante la prueba
- `GET /healthz/`
- logs del servicio
- login
- panel del despachador
- mapa en tiempo real
- respuesta general del sistema

### Criterios de aprobacion
- El servicio sigue disponible durante la prueba.
- `healthz` responde `200`.
- El panel despachador responde correctamente.
- El mapa muestra unidades ONLINE durante la simulacion.
- No aparecen errores graves repetidos.
- No hay errores finales de base de datos.
- La simulacion termina correctamente.

### Evidencia a guardar
- fecha y hora de la prueba.
- cantidad de empresas.
- cantidad de unidades.
- iteraciones ejecutadas.
- sesiones activas.
- GPS generados.
- heartbeats generados.
- estado final de `healthz`.
- resumen de logs.

### Si la prueba falla
- Guardar el error exacto.
- Guardar hora del incidente.
- Revisar logs de aplicacion y base de datos.
- Reducir alcance si es necesario y volver a probar.
- No pasar a empresa real hasta resolver la causa.

### Conclusion esperada
- Si 5 empresas x 70 unidades se aprueba en Render, el sistema queda con una validacion fuerte tanto en local como en servidor.
