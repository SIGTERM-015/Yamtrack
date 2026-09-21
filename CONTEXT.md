# Contexto de producto de Yamtrack

## Propósito

Yamtrack es un registro personal de medios. Este documento fija el vocabulario y los acuerdos de alcance confirmados para el fork; no describe funcionalidades implementadas ni convierte la intención de producto en un compromiso técnico.

## Términos

- **Medio**: contenido rastreable del catálogo de Yamtrack: película, serie, anime, manga, videojuego, libro, cómic, juego de mesa u otro tipo admitido.
- **Seguimiento**: relación entre una persona y un medio, con estado, progreso y otros datos de esa persona.
- **Grupo**: relación colaborativa de personas que siguen un medio juntas. El grupo coordina pendientes y actividad compartida, pero no sustituye los registros personales ni es un escaparate público. Al registrar consumo del grupo se eligen los participantes, con todos los miembros preseleccionados por defecto; añadir un pendiente al grupo lo añade como pendiente a todos los miembros, conservando los registros personales existentes y creando el pendiente solo si no existe.
- **Registro personal**: datos de seguimiento de una persona para un medio concreto. Incluye su progreso, valoración y comentario individuales.
- **Perfil**: representación pública de una persona y de su actividad de seguimiento. El perfil público deseado podrá mostrar avatar, heatmap y estanterías configurables, además del detalle de los medios y sus datos visibles.
- **Heatmap**: vista de actividad del perfil que refleja el consumo diario, es decir, avances y finalizaciones, con detalle al hacer clic en un día. Añadir pendientes no cuenta como consumo, ni la edición de opiniones o correcciones cuenta como consumo nuevo.
- **Estantería**: agrupación siempre manual de medios dentro de un perfil; funciona como escaparate de favoritos (por ejemplo, un TOP5). La persona configura su selección, orden, nombre y descripción, y puede mezclar tipos de medio. El catálogo completo se mantiene separado de las estanterías.
- **Catálogo**: conjunto de medios disponibles para búsqueda y seguimiento.
- **Metadatos y etiquetas**: los metadatos provienen del proveedor y se pueden completar manualmente cuando faltan; son distintos de las etiquetas personales. El filtro estricto excluye los medios con metadatos desconocidos y muestra cuántos quedan excluidos.
- **Estado y progreso**: datos de avance de un registro personal o de un grupo. El grupo registra el consumo conjunto y mantiene su propio estado y progreso contextuales, independientes de los personales. El avance del grupo actualiza los registros personales de los participantes elegidos al registrar el consumo (todos los miembros preseleccionados por defecto), pero nunca reduce su progreso personal ni reabre un registro ya completado; el avance individual no actualiza el grupo de forma automática. La corrección deliberada de progreso sigue acuerdo 17.
- **Valoración y comentario**: datos exclusivamente individuales; no existen como datos conjuntos del grupo en el alcance confirmado.
- **Integración**: consumidor externo de la API de Yamtrack mediante un token personal identificable y revocable, con permisos de lectura y escritura separados y ligado a la identidad de quien lo usa. La intención es permitir buscar obras y datos, consultar bibliotecas, historial y estadísticas propias o autorizadas, y escribir para añadir contenido, actualizar estados y progreso y gestionar las propias puntuaciones y comentarios.
- **Recomendador**: feature separada que descubre contenido nuevo basándose inicialmente en puntuaciones, géneros e historial, con prioridad para las preferencias explícitas. Completar no implica gusto y abandonar no implica rechazo. Inicialmente no analiza comentarios con IA ni los envía a servicios externos. Distingue recomendación general de personalizada y contempla el arranque en frío con géneros favoritos opcionales, historial disponible y populares por medio. Funciona en ámbito personal y grupal; en el grupal cada miembro pesa igual, con independencia de cuántas valoraciones tenga (acuerdo 34). Cuando escasean los candidatos, muestra menos resultados, explica por qué y ofrece ampliar los filtros o permitir explícitamente obras conocidas (acuerdo 35).
- **Ruleta**: feature separada para elegir contenido únicamente entre pendientes, con filtros de géneros y atributos del medio, como multijugador en videojuegos. Funciona en ámbito personal y grupal. El sorteo solo propone una opción sin modificar nada; se puede repetir o confirmar «Empezar», que cambia el estado según las reglas de ámbito. Cuando escasean los candidatos, muestra menos opciones, explica por qué y ofrece ampliar los filtros de forma explícita, siempre dentro de los pendientes: el universo de candidatos nunca cambia y no puede ofrecer obras fuera de ellos (acuerdo 35).
- **Estadísticas**: vistas de actividad y consumo derivadas de los registros personales y compartidos. No generan datos inexistentes: no se inventan horas de consumo sin datos y los episodios o avances cuentan como actividad, no como series completas.

- **No me interesa**: descarte reversible, distinto de una puntuación o del estado Dropped. En ámbito personal afecta solo a esa persona; en ámbito grupal, solo al grupo. Los descartes se podrán consultar y restaurar.

## Acuerdos confirmados

1. Se podrá hacer seguimiento de todos los medios de Yamtrack tanto individualmente como en pareja o grupo.
2. El progreso compartido no hace retroceder el progreso personal de ningún participante ni reabre registros completados. Por ejemplo, si una persona tiene progreso 8, la pareja 3 y el grupo 4, el resultado conserva 8 para la persona y 4 para el grupo. La corrección deliberada de progreso sigue acuerdo 17.
3. Cualquier miembro puede gestionar el contenido y el progreso del grupo. La persona propietaria gestiona el grupo y sus miembros.
4. Quitar un medio del grupo o salir de él conserva los registros personales, las valoraciones, las puntuaciones y los comentarios de cada persona.
5. Las valoraciones y comentarios son individuales, no conjuntos.
6. La acción secundaria desde el buscador será añadir un medio a un grupo.
7. La API de Yamtrack se plantea para integraciones con tokens personales identificables y revocables, con permisos de lectura y escritura separados y ligados a la identidad de quien los usa. Su alcance inicial cubre buscar obras y datos, consultar bibliotecas, historial y estadísticas propias o autorizadas, añadir contenido y actualizar estados y progreso personal y grupal, leer y editar las propias puntuaciones y comentarios, y quitar contenido de una biblioteca respetando los registros personales al salir de un grupo. Nadie puede editar puntuaciones ni comentarios ajenos. Las operaciones grupales usan los permisos de quien las ejecuta y las mismas reglas que la interfaz. Quedan fuera del alcance inicial crear grupos, gestionar miembros y configurar perfiles públicos.
8. Solo se contemplan perfiles públicos personales. Los grupos sirven para coordinar pendientes, contenido en curso y estados compartidos; no son un escaparate público grupal.
9. La ruleta y el recomendador son features separadas, disponibles en ámbito personal y grupal. La ruleta elige únicamente entre pendientes y admite filtros de géneros y atributos, como multijugador en videojuegos. El recomendador descubre contenido nuevo usando historial, valoraciones y métricas todavía no concretadas.
10. El grupo registra el consumo conjunto y posee estado y progreso contextuales independientes de los registros personales. El avance del grupo actualiza los registros personales participantes sin retrocesos ni reapertura de completados; el avance individual no actualiza el grupo de forma automática.
11. Al incorporarse una persona a un grupo, solo se incorporan los pendientes como pendientes y se conservan sus registros personales previos. No se le atribuye consumo pasado ni progreso de obras ya empezadas.
12. La ruleta elige únicamente entre pendientes, con filtros, tanto en ámbito personal como grupal. El sorteo propone una opción sin mutar el estado; permite repetir o confirmar «Empezar», que cambia el estado según las reglas de ámbito.
13. El recomendador propone contenido nuevo en ámbito personal y grupal. La combinación de gustos grupales sigue el acuerdo 34.
14. Las estadísticas contemplan cuatro vistas: completados y actividad por periodo y tipo; géneros consumidos y mejor valorados; distribución de puntuaciones; e individual frente a compartido.
15. Las estadísticas no inventan horas de consumo sin datos. Los episodios y avances cuentan como actividad, no como series completas.
16. La persona y la obra son conceptos separados. Las repeticiones reales de una obra cuentan para estadísticas y heatmap sin duplicar la obra. Puntuación y comentario son únicos por persona y obra y solo su autor puede editarlos.
17. Una corrección deliberada de progreso es una acción grupal y nunca silenciosa: se muestra una vista previa y se confirma antes de aplicarla. Solo puede revertir cambios personales atribuibles a esa acción del grupo, debe preservar los avances independientes posteriores y avisa cuando no puede determinar con seguridad qué es atribuible a la acción del grupo.
18. El recomendador grupal prioriza la afinidad compartida entre miembros y evita el dominio de un único miembro. Explica brevemente por qué recomienda, sin presentar certeza ficticia.
19. El descubrimiento en ámbito grupal excluye por defecto las obras completadas por cualquier miembro y ofrece la opción de permitir obras ya conocidas por algún miembro. Excluye siempre la propia biblioteca del grupo.
20. La visibilidad parte de la apertura: perfil personal público por defecto y privacidad opcional (opt-in). Los comentarios son públicos por defecto en un perfil público. La visibilidad se controla por secciones, no como un interruptor único.
21. No mostrar la pertenencia de una persona a grupos que el recomendador haya propuesto previamente y no hayan sido contradichos.
22. La apertura se diseña como estado inicial desde el principio, porque no hay usuarios ni datos existentes que preservar. No se contempla una migración de visibilidad ni una acción para publicar opiniones previas. Esta decisión es de diseño y no autoriza borrar datos de desarrollo.
23. Una misma actividad compartida vinculada a participantes cuenta una vez por cada participante y una vez para el grupo; no se duplica el mismo cómputo. El origen del registro distingue si es individual o grupal, no la coincidencia de obra y fecha.
24. El recomendador distingue recomendación general de personalizada. En arranque en frío admite géneros favoritos opcionales, aprovecha el historial disponible y completa con populares por medio. No descarta a un miembro por tener menos valoraciones.
25. Al registrar el consumo de un grupo se eligen los participantes, con todos los miembros preseleccionados por defecto. El avance del grupo actualiza solo los registros personales de los participantes elegidos. Añadir un pendiente al grupo lo añade como pendiente a todos los miembros, conservando los registros personales existentes y creando el pendiente solo si no existe: no hace retroceder el progreso personal ni reabre un registro completado. Esto reconcilia el consumo conjunto con los participantes: los participantes por defecto son todos los miembros, pero el grupo solo avanza los registros personales de quienes participan en ese registro.
26. El heatmap refleja el consumo diario (avances y finalizaciones) y permite abrir el detalle al hacer clic en un día. Añadir pendientes no cuenta como consumo, ni la edición de opiniones o correcciones cuenta como consumo nuevo.
27. Las estanterías son siempre manuales y funcionan como escaparate de favoritos (por ejemplo, un TOP5); no son una función exclusiva del MVP. La persona configura su selección, orden, nombre y descripción, puede mezclar tipos de medio y mantiene el catálogo completo separado de las estanterías.
28. En el detalle de perfil, el visitante ve la opinión, la puntuación, el estado y el historial de la persona propietaria, junto con la información de la obra, respetando la visibilidad configurada por secciones. El tracking del visitante es secundario.
29. Los metadatos provienen del proveedor y se pueden completar manualmente cuando faltan; se gestionan separados de las etiquetas personales. El filtro estricto excluye los medios con metadatos desconocidos y muestra cuántos quedan excluidos.

Estos puntos son intención de producto. No afirman que la implementación actual los soporte.

## Alcance de perfil público deseado

El perfil público deseado es personal y contempla avatar, heatmap, estanterías configurables y, por medio, una vista de detalle con información, valoración y comentario. En el detalle, el visitante ve la opinión, la puntuación, el estado y el historial de la persona propietaria, junto con la información de la obra, respetando la visibilidad por secciones; el tracking del visitante es secundario. No se contempla un perfil público de grupo.

Dirección confirmada: la apertura es la postura por defecto. El perfil personal es público por defecto y hacerlo privado es una opción explícita (opt-in); los comentarios son públicos por defecto en un perfil público. El control de visibilidad se ejerce por secciones, no como un único interruptor global.

La apertura se diseña como estado inicial del producto: no hay usuarios ni datos existentes que preservar, así que no se contempla migración de visibilidad ni una acción para publicar opiniones previas. Esta decisión es de diseño y no autoriza borrar datos de desarrollo.

30. Los filtros de la ruleta se podrán combinar entre categorías: tipo de medio, géneros, etiquetas personales, duración de películas y modalidades de videojuegos (single/cooperativo/competitivo y local/online), donde existan datos. Se mantienen las reglas de metadatos desconocidos y filtro estricto del acuerdo 29.
31. La ruleta usa azar uniforme entre los candidatos que cumplen los filtros. No repite candidatos durante la sesión hasta agotarlos y nunca relaja los filtros automáticamente.
32. «No me interesa» es un descarte reversible y separado de la puntuación y del estado Dropped. El descarte personal afecta solo a esa persona y el grupal solo al grupo; los descartes se podrán consultar y restaurar.
33. El recomendador inicial usa puntuaciones, géneros e historial y prioriza las preferencias explícitas. Completar un medio no implica que haya gustado y abandonarlo no implica odio o rechazo. Inicialmente no se analizan comentarios con IA ni se envían a servicios externos.

34. En el recomendador grupal cada miembro pesa igual, con independencia del número de valoraciones que tenga. La afinidad compartida penaliza las incompatibilidades claras sin vetar automáticamente por una diferencia. La popularidad apoya la recomendación, pero con pocas señales no se antepone a las preferencias.
35. Cuando escasean los candidatos, la ruleta y el recomendador muestran menos opciones y explican por qué. En la ruleta la única vía explícita es ampliar los filtros, siempre dentro de los pendientes: su universo de candidatos nunca cambia y no puede ofrecer obras fuera de ellos. En el recomendador, además de ampliar los filtros, se puede permitir explícitamente obras conocidas. Ninguna de las dos recupera descartes ni ignora restricciones en silencio.
36. La interfaz inicial no incluye controles numéricos de pesos. Los pesos concretos son una decisión de implementación, bajo las reglas de producto, a validar con ejemplos de gustos y pruebas; no son una pregunta de producto abierta.

## Preguntas abiertas

No quedan preguntas de producto abiertas sobre la ponderación del recomendador ni sobre la escasez de candidatos: los acuerdos 34-36 las cierran.

Detalles técnicos pendientes, que no son preguntas de producto:

- Elección y calibración de los pesos concretos del recomendador dentro de las reglas de los acuerdos 18, 33 y 34, validadas contra ejemplos de gustos y pruebas.
- Ajuste de la afinidad grupal con datos escasos o cuando todos han completado las obras del grupo, sin que domine un único miembro (acuerdo 18).
