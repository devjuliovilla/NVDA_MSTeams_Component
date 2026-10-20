# Teams Chat para NVDA

Complemento para el lector de pantalla [NVDA](https://www.nvaccess.org/) que facilita el uso de los chats del **nuevo Microsoft Teams de escritorio**.

Permite leer los últimos mensajes de un chat sin mover el foco ni buscarlos con el ratón, copiarlos y abrir sus enlaces y archivos adjuntos desde una ventana accesible.

> Versión 0.1.6. Probada con NVDA 2026.2 y Teams en español: lectura y copia de mensajes, ventana de acciones, enlaces, menú de opciones de Teams, adjuntos (visor, navegador, descarga y copiar dirección) y atajo al cuadro de redacción.

## Características

- **Leer mensajes con un atajo.** Alt+0 lee el último mensaje del chat abierto, Alt+1 el anterior y así hasta Alt+9, el décimo empezando por abajo. Funciona aunque el foco esté en el cuadro de redacción.
- **Tus mensajes se leen como "Tú"**, sin repetir tu nombre.
- **Respuestas con cita:** primero se lee el mensaje y después la cita: "Ana: de acuerdo. Mensaje citado de Luis: …".
- **Archivos adjuntos:** se anuncian al leer el mensaje: "Ana: te paso el informe. Adjunto: informe.pdf".
- **Copiar un mensaje** pulsando su atajo dos veces seguidas. Si el mensaje solo tiene un adjunto, copia la dirección del archivo.
- **Ventana de acciones del mensaje:** copiar el texto, abrir el menú de opciones de Teams, abrir o copiar enlaces, y abrir, descargar o copiar la dirección de los archivos adjuntos. La descarga usa el menú contextual del propio Teams, así que el archivo va directo a tu carpeta de descargas.
- **Ir al cuadro de redacción** desde cualquier parte del chat con un atajo.
- **Solo actúa en Teams.** En el resto de aplicaciones NVDA funciona exactamente igual que siempre.

## Atajos de teclado

Todos funcionan dentro de Teams, con la fila superior de números o con el teclado numérico (Bloq Num activado).

| Atajo | Acción |
|---|---|
| Alt+0 a Alt+9 | Lee el mensaje indicado: 0 es el último, 9 el décimo desde abajo |
| Alt+número dos veces seguidas | Copia el texto del mensaje al portapapeles |
| NVDA+Alt+0 a NVDA+Alt+9 | Abre la ventana de acciones del mensaje |
| NVDA+Alt+E | Lleva el foco al cuadro de redacción del chat |

En la ventana de acciones, las flechas recorren la lista, Intro ejecuta la acción seleccionada y Escape cierra sin hacer nada.

Los atajos se pueden cambiar en NVDA → Preferencias → Gestos de entrada, categoría **Teams Chat**.

### Atajos descartados y por qué

- **Control+Alt+número:** en los teclados en español, Control+Alt equivale a AltGr, y se perdería la posibilidad de escribir **@** (menciones) y **#** en Teams.
- **Alt+Mayúsculas y NVDA+Mayúsculas:** Mayúsculas + teclado numérico tiene un comportamiento especial en Windows, y Alt+Mayúsculas puede cambiar el idioma del teclado.

## Opciones

En NVDA → Preferencias → Opciones → **Teams Chat**:

- **Usar Alt + teclado numérico para leer mensajes.** Activada por defecto. Mientras está activada, dentro de Teams no se pueden escribir caracteres con códigos Alt (por ejemplo, Alt+0191 para "¿"). Fuera de Teams los códigos Alt funcionan con normalidad.
- **Acción por defecto para archivos adjuntos:** abrir en el visor de Teams, abrir en el navegador (por defecto) o descargar. Esa acción aparece la primera en la ventana de acciones.

## Requisitos

- NVDA 2024.1 o posterior.
- Nuevo Microsoft Teams de escritorio (`ms-teams.exe`). El Teams clásico y la versión web no están soportados.

Se ha probado con la interfaz de Teams en español. El complemento localiza los mensajes por identificadores internos de Teams y no por textos, así que la lectura de mensajes debería funcionar con otros idiomas. Dos excepciones: descargar adjuntos busca la opción "Descargar" del menú de Teams, así que de momento solo funciona con Teams en español, y los mensajes del propio complemento están en español.

## Instalación

1. Descarga el archivo `teamsChat-<versión>.nvda-addon` de la sección [Releases](https://github.com/devjuliovilla/NVDA_MSTeams_Component/releases) del repositorio, o genera el paquete tú mismo (ver más abajo).
2. Pulsa Intro sobre el archivo descargado.
3. Acepta la instalación y reinicia NVDA.

## Comentarios y errores

Si algo no funciona o tienes una idea, abre una incidencia en la sección [Issues](https://github.com/devjuliovilla/NVDA_MSTeams_Component/issues) del repositorio. Si es posible, pon el registro de NVDA en nivel "depuración", repite el problema e incluye las líneas del registro (NVDA+F1) que empiezan por `teamsChat:`. Revisa antes que no contengan nada privado.

## Desarrollo

### Estructura

```
addon/
  manifest.ini                 Nombre, versión y compatibilidad del complemento
  appModules/ms-teams.py       Lógica principal; NVDA solo la carga cuando Teams está activo
  globalPlugins/teamsChat.py   Panel de opciones (disponible aunque Teams no esté abierto)
build.ps1                      Empaqueta addon/ en dist/teamsChat-<versión>.nvda-addon
```

### Generar el paquete

Desde PowerShell, en la raíz del proyecto:

```powershell
./build.ps1
```

La versión se toma de `addon/manifest.ini`. El paquete se genera en `dist/`, que no se guarda en el repositorio.

### Cómo funciona

Teams es una aplicación web (WebView2) y NVDA la ve como un documento. El complemento:

1. Busca la lista de mensajes por su identificador `chat-pane-list`. Primero sube desde el foco, luego usa la última lista encontrada (si no ha cambiado el título de la ventana, es decir, el chat) y, como último recurso, busca en el documento con un límite de elementos.
2. Recorre la lista de abajo hacia arriba, saltando los encabezados, y localiza cada mensaje por los identificadores `message-body-<id>`, `author-<id>`, `content-<id>` y `attachments-<id>`.
3. Distingue tus mensajes por la clase `fui-ChatMyMessage`.

Las clases con nombres aleatorios de Teams (por ejemplo, `___1vlkg3r`) cambian con cada actualización, así que no se usan.

Con el nivel de registro de NVDA en "depuración" (NVDA → Preferencias → Opciones → General), cada lectura y cada acción se anotan en el registro (NVDA+F1) en líneas que empiezan por `teamsChat:`, con lo que ha tardado. En el nivel normal solo se anotan los errores, porque esas líneas pueden incluir nombres de archivos y textos del chat.

### Ramas

- `main`: versión estable.
- `develop`: desarrollo en curso.

## Seguridad y privacidad

- El complemento solo lee lo que Teams ya muestra en pantalla. No envía nada a ningún servicio.
- Solo abre direcciones `http` y `https`, nunca otros tipos de enlace que puedan llegar por un chat.

## Autor

Julio Villa.
