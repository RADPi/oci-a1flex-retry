# Reintento automático — Oracle Cloud A1.Flex (Always Free)

Este repo reintenta crear una instancia Ampere A1 Flex (Always Free) cada 5
minutos (el intervalo mínimo que permite GitHub Actions para workflows
programados) usando GitHub Actions, llamando directamente a la API de OCI
con una API signing key (no depende del navegador ni de la sesión de la
consola).

Prueba, en orden, varias combinaciones de OCPU/memoria (configurables en
`retry_oci_a1flex.py`); si Oracle devuelve "Out of capacity" en todas, la
corrida termina normal (no en rojo) y GitHub Actions la vuelve a intentar en
5 minutos. Cuando alguna funciona, crea un Issue en este repo avisando y se
autodeshabilita para no seguir corriendo.

Todos los identificadores de tu cuenta (OCIDs, región, nombre de la VCN, de
la subnet, de la instancia) se cargan como GitHub Secrets — el código de este
repo no contiene ningún dato identificable de una cuenta OCI en particular,
así que podés tenerlo en un repo público sin exponer nada más allá de "existe
esta automatización" (útil porque en un repo público los minutos de Actions
son ilimitados, en vez del tope de 2000 min/mes del plan Free en repos
privados).

## Por qué no lo subió Claude directamente

Por política de seguridad, Claude no puede iniciar sesión en tu cuenta de
GitHub (ni con contraseña guardada ni con 2FA) ni pegar API keys/tokens en
ningún formulario web. Por eso te dejo todo listo acá para que **vos** crees
el repo y cargues los datos en 5 minutos, sin que yo toque tus credenciales.

## Paso 1 — Crear el repo

1. Entrá a GitHub (podés hacerlo desde el celular) y creá un repositorio
   nuevo, por ejemplo `oci-a1flex-retry`. Podés elegir público o privado —
   ver la nota sobre minutos de Actions más abajo.
2. Subí los 3 archivos de esta carpeta manteniendo la estructura:
   - `retry_oci_a1flex.py`
   - `.github/workflows/retry-a1flex.yml`
   - `README.md` (opcional, solo de referencia)

   Más fácil desde el celular: en GitHub, "Add file" → "Upload files" y
   arrastrás los 3 (para el workflow, asegurate de que quede en la ruta
   `.github/workflows/retry-a1flex.yml` — si el uploader no respeta
   subcarpetas, subilo desde github.com en escritorio o con la app).

## Paso 2 — Cargar los secretos

En el repo: **Settings → Secrets and variables → Actions → New repository
secret**. Cargá estos 9, uno por uno:

| Nombre | Qué va ahí |
|---|---|
| `OCI_USER_OCID` | El OCID de tu usuario OCI (`ocid1.user.oc1..xxxx`) |
| `OCI_FINGERPRINT` | El fingerprint de la API key que agregamos en OCI Console |
| `OCI_TENANCY_OCID` | El OCID de tu tenancy (`ocid1.tenancy.oc1..xxxx`) |
| `OCI_REGION` | Tu región de OCI (ej. `sa-saopaulo-1`) |
| `OCI_PRIVATE_KEY` | Contenido completo del archivo `.pem` de la API key que te mandé aparte (incluyendo las líneas `-----BEGIN PRIVATE KEY-----` y `-----END PRIVATE KEY-----`) |
| `OCI_SSH_PUBLIC_KEY` | Tu clave pública SSH (`ssh-rsa AAAA...`) |
| `OCI_VCN_NAME` | El nombre de tu VCN en OCI |
| `OCI_SUBNET_NAME` | El nombre de tu subnet en OCI |
| `OCI_INSTANCE_NAME` | El nombre que querés que tenga la instancia a crear |

Los primeros 4 y los últimos 3 son identificadores, no secretos en sentido
estricto, pero los cargamos como Secret igual para que el repo (código,
README, logs) no contenga ningún dato identificable de tu cuenta — así se
puede tener público sin exponer más que "existe esta automatización". Los
que sí son sensibles de verdad son `OCI_PRIVATE_KEY` (lo único que permite
firmar pedidos a la API de tu cuenta OCI) y, en menor medida,
`OCI_SSH_PUBLIC_KEY` (esta es pública por diseño, no hay problema si se ve).

Nada de esto lo tipeé yo en ningún formulario: la key ya está subida como
API key pública en tu cuenta de OCI (la agregué yo desde la consola, con tu
sesión ya abierta, igual que hicimos antes con la SSH key), pero la carga
de los secrets en GitHub la tenés que hacer vos.

## Paso 3 — Listo

En cuanto guardes los 9 secrets, andá a la pestaña **Actions** del repo y
activá el workflow si te lo pide ("I understand my workflows, go ahead and
enable them"). A partir de ahí corre solo cada 5 minutos.

Podés ver el progreso en Actions → "Retry OCI A1.Flex instance" → cada
corrida muestra en los logs si encontró capacidad o no. Cuando tenga éxito,
te va a llegar una notificación de GitHub por el Issue que crea
automáticamente (si tenés notificaciones activadas para este repo).

## Notas

- **Consumo de minutos:** con el repo público, Actions es ilimitado, así que
  el cron está en `*/5 * * * *` (el mínimo que admite GitHub — no se puede
  programar más seguido que cada 5 min). Si en algún momento volvés a un repo
  privado, cambiá esa línea en `.github/workflows/retry-a1flex.yml` a
  `*/15 * * * *` o `*/30 * * * *` para no comerte los 2000 min/mes gratis del
  plan Free.
- **¿Vale la pena ir más seguido?** GitHub no permite bajar de 5 min. Además,
  la API de OCI tiene su propio rate limiting para desalentar el polling
  agresivo (el script lo maneja solo, ver `is_rate_limit_error`), y cuando se
  abre una ventana de capacidad libre normalmente la agarran otros scripts
  automatizados corriendo en paralelo en cuestión de segundos — así que la
  frecuencia ayuda un poco pero no es determinante.
- **Secrets y logs:** GitHub oculta los Secrets en la interfaz y redacta
  automáticamente cualquier substring que coincida con un Secret configurado
  en los logs de las corridas, aunque el script no lo imprima a propósito
  (por ejemplo, la región queda tapada con `***` si aparece dentro de un
  OCID que el script sí imprime). El script tampoco imprime nunca
  `OCI_PRIVATE_KEY` en ningún `print()`.
- **Inactividad:** GitHub deshabilita automáticamente los workflows
  programados si el repositorio no tiene ningún commit en 60 días. Si pasa
  mucho tiempo sin que se libere capacidad, puede que tengas que
  re-habilitarlo manualmente desde Actions (o hacer un commit cualquiera).
- **Cambiar de shape/config:** la lista de combinaciones OCPU/GB a probar
  está en `SHAPE_CONFIGS`, al principio de `retry_oci_a1flex.py`. El resto
  de los ajustes (VCN, subnet, nombre de instancia, SO) son todos variables
  de entorno — no hace falta tocar código para cambiarlos, solo el valor del
  Secret correspondiente.
- **Revocar el acceso:** si en algún momento querés cortar el acceso de esta
  automatización a tu cuenta OCI, andá a OCI Console → tu perfil → Tokens and
  keys → API keys, y borrá la key con el fingerprint que cargaste en
  `OCI_FINGERPRINT`.
