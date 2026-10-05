# ¿Pedir «start date» y «end date» en los formularios de seguimiento?

Fecha: 2026-10-05 · Solo análisis, sin cambios de código.

## 1. Qué depende de cada fecha

Ambas viven en el registro personal abstracto (`src/app/models.py:994-995`); el episodio solo tiene `end_date` (`models.py:1905`). En TV y temporada son propiedades derivadas de los episodios (`models.py:1186-1201`, `1670-1688`), y sus formularios ya no las piden (`src/app/forms.py:339-363`).

**`end_date` sostiene casi todo lo que el fork considera consumo:**
- Heatmap (`src/app/statistics.py:640-660`), actividad reciente (`statistics.py:910-955`), diario/timeline del perfil (`statistics.py:978-1060`): solo cuentan registros con `end_date`.
- «Terminado el» en el detalle de perfil (`src/app/views.py:339-349`), fecha de la tarjeta (`templates/app/components/media_card.html:21-24`) y orden «End Date» (`src/users/models.py:80`, `models.py:352-400`).
- Importadores: Trakt, Letterboxd, Goodreads, MAL y AniList la rellenan (`integrations/imports/trakt.py:453`, `letterboxd.py:241`, `goodreads.py:264`, `mal.py:191`, `anilist.py:330`). También los webhooks (`webhooks/movie.py:104`, `anime.py:57`) y los grupos (`groups/services.py:533-555`).

**`start_date` pesa poco:** filtro de estadísticas por solapamiento de rango (`statistics.py:117-142`), timeline de la API (`statistics.py:465-530`), orden «Start Date», historial (`history_processor.py:516`) y respaldo de la tarjeta. No alimenta ni el heatmap ni el diario.

## 2. Qué se rellena solo hoy

- **Servidor:** solo pone `end_date` cuando el progreso alcanza el máximo estando en curso (`models.py:1040-1054`). Pasar a Completed por estado (`process_status`, `models.py:1056-1067`) **no** pone `end_date`, y tampoco lo hace la API.
- **Cliente:** `src/static/js/mediaStatusDateHandler.js:37-110` propone `end_date = ahora` al elegir Completed y `start_date = ahora` al elegir In progress, solo si el campo está vacío y visible.

Si se ocultan los campos tal cual, el autorrelleno queda en manos de un JS sobre inputs ocultos. Una película marcada como Completed por la API o sin JS se quedaría sin `end_date` y desaparecería del heatmap, del diario y de las estadísticas por periodo. Esto ya pasa hoy si alguien borra la fecha.

## 3. Por tipo de medio

| Tipo | ¿Hace falta un rango? | Qué conviene |
|---|---|---|
| Película | No | Una «fecha vista» (= `end_date`) |
| Serie / temporada | Ya no lo pide | Fecha por episodio, como ahora |
| Episodio | No | `end_date`, ya existe |
| Anime | Poco; el progreso se registra por episodio, pero no deja fecha por episodio | Fin automático; el inicio es útil a veces |
| Manga / cómic | Sí para series largas: «empecé en marzo y lo acabé en junio» se usa | Rango opcional |
| Libro | Sí: es el caso clásico (Goodreads lo modela así) | Rango visible |
| Videojuego | Sí: se juegan durante semanas y Steam/HLTB no aportan fechas | Rango opcional |
| Juego de mesa | No: el progreso son partidas sueltas | Una fecha o ninguna |

El usuario acierta en película, serie y juego de mesa. Para libro, videojuego y manga el rango sí aporta. Además, la API y el filtro de estadísticas por solapamiento lo aprovechan: un libro leído de enero a marzo cuenta en los tres meses.

## 4. Propuestas

**A (recomendada): fechas automáticas en el servidor y plegadas en la UI.**
1. Pasar el autorrelleno al modelo: al pasar a Completed sin `end_date`, poner `end_date = ahora`; al pasar a In progress sin `start_date`, poner `start_date = ahora`. Así funciona igual en formulario, API y grupos.
2. Película y juego de mesa: un único campo «Fecha» (= `end_date`), sin `start_date`.
3. Libro, videojuego, manga, cómic y anime: las fechas pasan a un bloque «Más detalles» plegable, que se abre solo si ya tienen valor.
4. Borrar el JS de autorrelleno, o reducirlo a mostrar el valor propuesto.

- **Impacto en datos:** ninguno; no se migra nada. Los `start_date` existentes en películas se conservan, aunque ya no se puedan editar desde la UI (la API sí).
- **Impacto en estadísticas:** mejora. Ningún Completed se queda sin fecha, así que el heatmap y el diario dejan de perder entradas.

**B: ocultar siempre las fechas y rellenarlas automáticamente.** Es lo más simple, pero impide corregir la fecha de un libro o de una película vista ayer, y en este fork esas correcciones alimentan el heatmap y el perfil público.

**C: dejarlo como está.** Mantiene el ruido en películas y el hueco del servidor.

## Archivos a tocar (opción A)

- `src/app/models.py` (`process_status`, ~1056): autorrelleno de fechas en el servidor.
- `src/app/forms.py:200-330`: `MovieForm` y `BoardgameForm` solo con `end_date` y etiqueta «Date»; el resto sigue con ambas.
- `src/templates/app/components/fill_track.html`: bloque «Más detalles» plegable.
- `src/templates/app/create_entry.html:320-335`: el mismo criterio.
- `src/static/js/mediaStatusDateHandler.js`: simplificar o eliminar.
- `src/api/helpers.py:33-82`: quitar `start_date` de las películas (opcional).
- Tests de formularios, de modelos (autorrelleno) y de la API.
