# ADR 0001: Alcance de seguimiento social e integraciones del fork

- **Estado:** aceptado como alcance de producto
- **Fecha:** 2026-09-20
- **Decisor:** producto

## Contexto

Yamtrack ya organiza el seguimiento de medios. El fork necesita expresar el alcance deseado para seguimiento individual y compartido, perfiles, integraciones y descubrimiento sin confundir acuerdos de producto con capacidades implementadas.

El modelo debe conservar la responsabilidad personal: una persona puede seguir un medio por su cuenta o participar en el seguimiento de ese medio con otra persona o un grupo. El grupo coordina estado y progreso, pero las opiniones permanecen ligadas a cada persona.

## Decisión

1. El alcance incluye seguimiento de todos los tipos de medios de Yamtrack de forma individual y en pareja o grupo.
2. El progreso compartido actualiza los registros personales participantes sin hacer retroceder el progreso personal ni reabrir registros completados. Por ejemplo, con progreso personal 8, de pareja 3 y de grupo 4, el resultado conserva 8 para la persona y 4 para el grupo. La corrección deliberada de progreso sigue acuerdo 17.
3. Cualquier miembro puede gestionar el contenido y el progreso del grupo. La persona propietaria gestiona el grupo y sus miembros.
4. Quitar contenido del grupo o salir de él conserva los registros personales, puntuaciones, valoraciones y comentarios de cada persona.
5. Las valoraciones y comentarios son siempre individuales; no se modelan como valoración o comentario conjunto.
6. Desde el buscador, la acción secundaria prevista es añadir el contenido a un grupo.
7. La API de Yamtrack se plantea para integraciones con tokens personales identificables y revocables, con permisos de lectura y escritura separados y ligados a la identidad de quien los usa. Su alcance inicial cubre buscar obras y datos, consultar bibliotecas, historial y estadísticas propias o autorizadas, añadir contenido y actualizar estados y progreso personal y grupal, leer y editar las propias puntuaciones y comentarios, y quitar contenido de una biblioteca respetando los registros personales al salir de un grupo. Nadie puede editar puntuaciones ni comentarios ajenos. Las operaciones grupales usan los permisos de quien las ejecuta y las mismas reglas que la interfaz. Quedan fuera del alcance inicial crear grupos, gestionar miembros y configurar perfiles públicos.
8. Solo se contemplan perfiles públicos personales. El perfil público deseado incluye avatar, heatmap, estanterías configurables y detalle por medio con información, valoración y comentario. Los grupos sirven para coordinar pendientes, contenido en curso y estados compartidos, no para funcionar como escaparate público grupal.
9. La ruleta y el recomendador son features separadas, disponibles en ámbito personal y grupal. La ruleta elige únicamente entre pendientes y admite filtros de géneros y atributos, como multijugador en videojuegos. El recomendador descubre contenido nuevo basándose en historial, valoraciones y métricas todavía por definir.
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

30. Los filtros de la ruleta se podrán combinar entre categorías: tipo de medio, géneros, etiquetas personales, duración de películas y modalidades de videojuegos (single/cooperativo/competitivo y local/online), donde existan datos. Se mantienen las reglas de metadatos desconocidos y filtro estricto del acuerdo 29.
31. La ruleta usa azar uniforme entre los candidatos que cumplen los filtros. No repite candidatos durante la sesión hasta agotarlos y nunca relaja los filtros automáticamente.
32. «No me interesa» es un descarte reversible y separado de la puntuación y del estado Dropped. El descarte personal afecta solo a esa persona y el grupal solo al grupo; los descartes se podrán consultar y restaurar.
33. El recomendador inicial usa puntuaciones, géneros e historial y prioriza las preferencias explícitas. Completar un medio no implica que haya gustado y abandonarlo no implica odio o rechazo. Inicialmente no se analizan comentarios con IA ni se envían a servicios externos.

34. En el recomendador grupal cada miembro pesa igual, con independencia del número de valoraciones que tenga. La afinidad compartida penaliza las incompatibilidades claras sin vetar automáticamente por una diferencia. La popularidad apoya la recomendación, pero con pocas señales no se antepone a las preferencias.
35. Cuando escasean los candidatos, la ruleta y el recomendador muestran menos opciones y explican por qué. En la ruleta la única vía explícita es ampliar los filtros, siempre dentro de los pendientes: su universo de candidatos nunca cambia y no puede ofrecer obras fuera de ellos. En el recomendador, además de ampliar los filtros, se puede permitir explícitamente obras conocidas. Ninguna de las dos recupera descartes ni ignora restricciones en silencio.
36. La interfaz inicial no incluye controles numéricos de pesos. Los pesos concretos son una decisión de implementación, bajo las reglas de producto, a validar con ejemplos de gustos y pruebas; no son una pregunta de producto abierta.

Estos acuerdos describen intención de producto, no una afirmación sobre la implementación actual ni un contrato técnico final de API.

## Fuera de esta decisión

Las preguntas de producto sobre la ponderación del recomendador y la escasez de candidatos quedan cerradas por los acuerdos 34-36.

Siguen como detalle técnico de implementación, no como pregunta de producto, la elección y calibración de los pesos concretos del recomendador dentro de las reglas de los acuerdos 18, 33 y 34, y el ajuste de la afinidad grupal con datos escasos o cuando todos han completado las obras del grupo.
