---
doc_id: ejemplos-estructuras
titulo: Ejemplos de estructuras de contenido y errores comunes
cms: generico
origen: demostracion_educativa
---

> **Documento de demostración con fines educativos.** Los ejemplos ilustran las reglas de demostración de
> este repositorio; no provienen de documentación oficial.

## Ejemplo: la misma estructura evaluada con distintas reglas

Un archivo con las propiedades `itemId`, `language`, `template` y `fields` (con `Title` y `Body`) cumple las
reglas SC-001 a SC-003 de la demostración de Sitecore. El mismo archivo, evaluado con las reglas UM-001 a
UM-003 de la demostración de Umbraco, falla: faltan `key`, `culture`, `contentType` y `properties`, y
`itemId`, `language`, `template` y `fields` se reportan como propiedades inesperadas en la raíz. Por eso es
imprescindible conocer el CMS de destino antes de decidir si un archivo es apto para importar.

## Ejemplo: errores estructurales frecuentes

Errores habituales en archivos localizados: coma final después del último elemento, comillas simples en
lugar de dobles, propiedad obligatoria omitida en la versión traducida, número escrito como cadena
(`"version": "2"`), y marcadores de posición alterados durante la traducción (`{0}` convertido en
`{ 0 }`).

## Buenas prácticas de Localization Engineering

Validar la sintaxis antes que la estructura, comparar cada idioma contra el idioma de origen, conservar los
marcadores de posición y las etiquetas HTML, no modificar nunca los archivos del cliente y reportar con
precisión la ruta de cada propiedad afectada para que el corrector pueda ubicarla sin buscar.
