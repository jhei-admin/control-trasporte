## Validacion de Escala Local

### Resultado final
- Sistema validado en entorno local para 5 empresas con 70 unidades por empresa.
- Simulacion GPS ejecutada con historico y sin historico.

### Pruebas aprobadas
- 2 empresas x 70 unidades sin historico: aprobado.
- 2 empresas x 70 unidades con historico: aprobado.
- 5 empresas x 70 unidades sin historico: aprobado.
- 5 empresas x 70 unidades con historico: aprobado.

### Evidencia clave
- Sesiones: 350
- Iteraciones: 4200
- Ubicaciones: 4200
- GPS: 4200
- Heartbeats: 4200

### Comportamiento observado
- El panel del despachador respondio correctamente.
- El mapa en tiempo real mostro unidades ONLINE durante la simulacion.
- No hubo apagado de PC en la prueba validada.
- No hubo errores finales de `database is locked` ni `disk I/O error` en la corrida aprobada.
- La carga GPS con historico termino correctamente.

### Conclusion
- El sistema puede considerarse apto para operar con al menos 5 empresas y 70 unidades por empresa bajo simulacion GPS completa.

### Limite de la validacion
- Esta validacion fue hecha en local.
- No confirma todavia capacidad para mas de 5 empresas.
- En produccion con PostgreSQL en Render, el comportamiento deberia ser igual o mejor que en esta prueba local.

### Siguiente nivel recomendado
- 10 empresas x 70 unidades.
- Varios despachadores conectados al mismo tiempo.
- Prueba prolongada de 30 a 60 minutos con `--sleep-real`.
