# ADR 0002: Rediseño de la UI de grupos y descarte

- **Estado:** aceptado como alcance de producto
- **Fecha:** 2026-09-23
- **Decisor:** producto

## Contexto

La UI actual de grupos pone la gestión antes que el uso: el detalle (`group_detail.html`) muestra la caja de invitar y la lista de miembros por encima de los medios, y no permite añadir medios al grupo (el servicio `add_item_to_group` existe pero ninguna vista lo llama). No hay pestañas, ni buscador dentro del grupo, ni ruleta; el registro de consumo se resuelve con radios binarios «Just me / Whole group» en lugar de selección de participantes; y la comparativa de valoraciones y las estadísticas de géneros viven en páginas separadas. El rediseño ordena la pantalla por prioridad de uso y fija las reglas del descarte («No me interesa»).

## Decisión

1. La pantalla del grupo se ordena por prioridad de uso: (1) añadir medios, (2) decidir qué ver, (3) actualizar el estado, (4) gestionar el grupo.
2. El diseño es genérico para N personas, no específico de pareja: contadores tipo «X/Y».
3. El grupo tiene cinco pestañas: **Pendientes** (con botón de ruleta) · **Viendo** · **Vistos** · **Estadísticas** (comparativa de valoraciones y géneros) · **Ajustes** (miembros, invitaciones, salir). Pendientes es la pestaña por defecto. Las tres primeras se basan en el estado propio del grupo, no en la suma de registros personales. *(Enmendado el 2026-10-05: las pestañas de estado se sustituyen por secciones apiladas como en Home; ver «Disposición como Home». Estadísticas y Ajustes siguen siendo páginas propias.)*
4. El estilo es la rejilla de pósters del resto de la app, con indicador de qué miembros lo han visto.
5. Añadir usa el botón «añadir a grupo» de la ficha del medio y de los resultados de la búsqueda general. *(Enmendado el 2026-10-01: se retira el buscador dentro del grupo; duplicaba la búsqueda general sin aportar nada en el uso real.)*
6. Ruleta y recomendador no son exclusivos de grupo: existen en ámbito individual y de grupo.
7. El estado y el progreso son propios del grupo, según la spec de CONTEXT.md: el grupo tiene estado y progreso contextuales; al avanzar actualiza los registros personales sin reducir progreso ni reabrir completados.
8. Quitar un medio del grupo lo puede hacer cualquier miembro; solo lo quita del grupo, los registros personales se conservan.
9. El **descarte** («No me interesa») se marca desde la ruleta y el recomendador; excluye el medio de futuras tiradas y recomendaciones; es reversible. Existe en ámbito individual y de grupo:
   - En grupo basta con que un miembro descarte para que desaparezca para todo el grupo; cualquier miembro puede recuperarlo.
   - Individual y grupal son independientes: descartar para ti no lo descarta en tus grupos, y viceversa.
   - Recuperación: sección «Descartados» (en recomendaciones para lo individual, en el grupo para lo grupal) con botón «recuperar», más «deshacer» inmediato tras descartar.

## Consecuencias

Hay que construir las cinco pestañas con el estado propio del grupo como fuente, el botón «añadir a grupo» en ficha y búsqueda, la ruleta de grupo sobre pendientes, la comparativa y los géneros integrados en la pestaña de estadísticas, y el flujo de descarte con «deshacer» y sección «Descartados» en ambos ámbitos. La gestión de miembros e invitaciones se repliega a la pestaña de Ajustes.

## Progreso del grupo

Decisiones tomadas durante la implementación de S4 (propagación de episodios de TV)
y S5 (rediseño de la pantalla de grupo), complementarias a la §7 de la Decisión:

1. **Paused/Dropped del grupo van a un filtro «Otros», no se propagan.** Las tres
   pestañas Pendientes/Viendo/Vistos se basan en el estado propio del grupo
   (Planning/In progress/Completed); un grupo en Paused o Dropped no encaja en
   ninguna sin distorsionar su semántica, así que ambos caen en un filtro «Otros»
   separado. Ni Paused ni Dropped del grupo se propagan a los registros
   personales de los participantes (solo In progress y Completed avanzan algo).
   *(Desde el 2026-10-05 Paused y Dropped tienen cada uno su sección en vez del
   filtro «Otros»; la regla de no propagación no cambia.)*
2. **Un miembro con registro personal Dropped se respeta, no se reanima.** Al
   marcar progreso o estado desde el grupo, la lista de participantes preselecciona
   a todos los miembros excepto a quien ya tenga el medio en Dropped en su perfil
   personal: aparece sin marcar por defecto (puede marcarse a mano si alguien
   quiere reactivarlo explícitamente). Esto es consistente con que Dropped es
   terminal para la propagación (ADR 0001 acuerdo 17).
3. **Los episodios de grupo se pueden marcar en cualquier orden.** El ledger
   `GroupEpisodeWatch` es un conjunto de episodios vistos, no un puntero de
   "último episodio", así que no impone secuencialidad; existe además el atajo
   «hasta el episodio N», que expande a marcar los episodios 1..N de una
   temporada de una sola vez.
4. **Un episodio visto por el grupo cuenta una sola vez.** El ledger tiene una
   fila por `(group_item, item)` con restricción de unicidad; volver a marcar el
   mismo episodio no lo duplica ni lo cuenta dos veces en el progreso agregado
   del grupo.

## Selección y acciones en la rejilla

*(Sustituida el 2026-10-05 por el punto 4 de «Disposición como Home».)*

Enmienda del 2026-10-01, tras uso real:

1. Las casillas de selección no están siempre visibles: aparecen al pasar el ratón (o con el foco) sobre un póster. Al seleccionar uno, todos muestran su casilla y tocar un póster lo marca o desmarca.
2. La barra de acciones masivas solo aparece con dos o más seleccionados. Con uno o ninguno, cada póster ofrece un selector de acción rápida (cambiar el estado del grupo, marcar episodios en series o abrir el resto de opciones).

## Disposición como Home

Enmienda del 2026-10-05, para que el grupo sea coherente con el resto de la app:

1. La página del grupo se organiza como Home: una sección por estado propio del grupo (In Progress, Planning, Completed, Paused, Dropped, en ese orden) y, dentro de cada una, una sub-sección por tipo de medio (en el orden de tipos de la app) con su rejilla de pósters. Cabecera de sección, contador y divisor son el mismo componente que usa Home. In Progress y Planning se muestran siempre (con estado vacío); el resto, solo si tienen algo. Paused y Dropped dejan de mezclarse en un filtro «Otros»: cada uno tiene su sección.
2. Se pasa de pestañas a secciones apiladas. Las pestañas obligaban a cambiar de vista para ver qué está en curso y qué queda pendiente, que es justo lo que Home enseña de un vistazo; con secciones, la página se lee igual que Home. Para no perder el acceso directo, una fila de enlaces bajo la cabecera salta a cada sección con su contador. Los enlaces antiguos `?tab=planning|in_progress|completed|other` abren la página completa; la API mantiene su parámetro `tab`.
3. Las tarjetas son las de Home: póster, título centrado y barra de color del estado del grupo, sin selector ni línea de estado. El contador «X/N» de miembros que lo han completado sigue como insignia sobre el póster. Las acciones aparecen al pasar el ratón con los mismos botones redondos de Home: actualizar el estado del grupo (o marcar episodios en series) y «más acciones» (participantes, «solo yo», «No me interesa», quitar del grupo). En pantallas táctiles, sin hover, un botón «⋯» visible sobre el póster abre las mismas acciones.
4. La selección se activa con un botón «Select» en la cabecera (o con la casilla que aparece al pasar el ratón por un póster). En modo selección todas las casillas son visibles, la tarjeta seleccionada lleva borde índigo y tocar un póster lo marca; «Cancel» o Escape salen. La barra de acciones masivas aparece desde el primer seleccionado (antes, desde dos) e incluye los participantes afectados. Esto sustituye la enmienda «Selección y acciones en la rejilla» del 2026-10-01.
5. El resumen de valoraciones del grupo y el enlace a «No me interesa» se muestran siempre, no solo en la pestaña Pendientes, porque ya no hay pestañas.

## Alternativas descartadas

- **Diseño específico de pareja:** se descarta por el acuerdo 2; el diseño genérico con contadores «X/Y» cubre la pareja sin casos especiales.
- **Tabla compacta en lugar de rejilla de pósters:** se descarta por el acuerdo 4; la rejilla mantiene la coherencia visual con el resto de la app.
- **Ruleta solo de grupo:** se descarta por el acuerdo 6; la ruleta existe en ambos ámbitos.
- **Descarte por unanimidad en grupo:** se descarta por el acuerdo 9; basta un miembro para descartar y cualquiera puede recuperar.
- **Descarte individual que se propaga al grupo:** se descarta por el acuerdo 9; los ámbitos son independientes.
- **Pestañas de estado con sub-secciones por tipo dentro:** se descarta por la enmienda «Disposición como Home»; obliga a cambiar de pestaña para ver lo que Home muestra junto, y deja el grupo con un sistema de navegación distinto al de Home.
