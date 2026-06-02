## Limpieza Operacion Real

### Objetivo
- conservar solo la empresa operativa real,
- validar que las unidades activas coincidan con la operacion,
- eliminar residuos de demo o de pruebas de escala de forma segura.

### Estado actual esperado
- empresa real: `Luren`
- unidades activas esperadas: `22`
- servidor actual: Render con PostgreSQL

### Paso 1: auditoria segura
Ejecutar primero en Render Shell o consola del servicio:

```powershell
python manage.py sanear_operacion_real --empresa-real "Luren" --unidades-esperadas 22
```

Esto no borra nada. Solo muestra:
- empresa real detectada,
- cantidad de vehiculos activos y totales,
- empresas extra,
- usuarios fuera de la empresa real,
- grupos demo.

### Paso 2: aplicar saneamiento
Solo cuando la auditoria confirme que todo lo extra es de prueba:

```powershell
python manage.py sanear_operacion_real --empresa-real "Luren" --unidades-esperadas 22 --aplicar --confirmar-empresa "Luren"
```

Opcionales:

```powershell
python manage.py sanear_operacion_real --empresa-real "Luren" --unidades-esperadas 22 --aplicar --confirmar-empresa "Luren" --eliminar-usuarios-huerfanos --eliminar-grupos-demo
```

### Paso 3: limpieza operativa normal
Despues del saneamiento conviene limpiar historicos viejos, sin tocar la empresa real:

```powershell
python manage.py limpiar_historicos --dry-run
python manage.py limpiar_historicos
```

### Paso 4: verificacion final
Revisar en admin:
- `Empresas`: debe quedar solo `Luren`
- `Vehiculos`: deben quedar `22`
- `Sesion unidades`: solo las operativas
- `Ubicacion vehiculos`: solo las actuales
- `Comandos de dispositivos` y `Estados de dispositivos`: sin residuos de pruebas

### Nota local
- Si necesitas revisar una SQLite especifica, define `SQLITE_NAME` explicitamente antes de correr `manage.py`.
- No ejecutar este saneamiento directo sobre una base local vieja si sospechas corrupcion SQLite. Para la operacion real, hacerlo en Render sobre PostgreSQL.
