Coloca aqui el APK firmado mas reciente de la app conductor.

Nombre sugerido:
- `gpsflotaaqp-latest.apk`

Flujo:
1. Genera el APK release firmado desde `GPSFlotaCompose`.
2. Copia el archivo a esta carpeta con el nombre configurado.
3. Haz `git add`, `git commit` y `git push`.
4. En Render configura:
   - `APP_LATEST_VERSION_CODE`
   - `APP_LATEST_VERSION_NAME`
   - `APP_UPDATE_CHANGELOG` (opcional)
   - `APP_UPDATE_FORCE` (opcional)

Si prefieres alojar el APK fuera del proyecto, define `APP_UPDATE_APK_URL`
y el backend devolvera esa URL en vez de servir el archivo local.
