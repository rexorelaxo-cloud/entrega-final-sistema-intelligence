---
doc_id: reglas-generales
titulo: Reglas generales de validación de contenido localizado
cms: generico
origen: demostracion_educativa
---

> **Documento de demostración con fines educativos.** Estas reglas fueron redactadas para este proyecto
> y no son documentación oficial de ningún producto.

## REGLA GEN-001 — Sintaxis JSON y codificación
- aplicabilidad: obligatoria
- severidad: ERROR
- verificacion: sintaxis

Todo archivo de contenido debe ser JSON válido, codificado en UTF-8, no vacío y sin claves duplicadas dentro
de un mismo objeto. Un archivo con errores de sintaxis (comas sobrantes, comillas sin cerrar, llaves
desbalanceadas) no puede importarse y debe corregirse antes de cualquier otra validación.

## REGLA GEN-002 — Consistencia estructural entre idiomas
- aplicabilidad: obligatoria
- severidad: ERROR
- verificacion: comparacion_estructural

Todas las versiones de idioma de un mismo ítem deben compartir exactamente el mismo conjunto de propiedades
y los mismos tipos de dato. Una propiedad presente en el idioma de origen y ausente en el idioma de destino
(o con distinto tipo) provoca importaciones incompletas o rechazadas. Las listas pueden tener distinta
cantidad de elementos solo si la diferencia está justificada; se reporta como información.

## REGLA GEN-003 — Consistencia de contenido entre idiomas
- aplicabilidad: obligatoria
- severidad: WARNING
- verificacion: consistencia_idiomas

Los valores de texto traducidos deben conservar los marcadores de posición ({0}, {{nombre}}, %s) y las
etiquetas HTML del texto de origen. No deben quedar valores vacíos cuando el origen tiene contenido. El
idioma declarado dentro del archivo debe coincidir con el indicado en el nombre del archivo y no debe haber
dos archivos para el mismo idioma.

## REGLA GEN-004 — Buenas prácticas de nomenclatura de archivos
- aplicabilidad: opcional
- severidad: INFO
- verificacion: ninguna

Se recomienda nombrar los archivos con el código de idioma como sufijo (por ejemplo `articulo.es-AR.json`),
usar UTF-8 sin BOM y mantener un archivo por idioma e ítem. Es una recomendación de organización; no
impide la importación.
