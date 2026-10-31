---
doc_id: umbraco-demo
titulo: Requisitos de importación de contenido para Umbraco (versión simplificada de demostración)
cms: umbraco
origen: demostracion_educativa
---

> **Documento de demostración con fines educativos.** Describe una estructura simplificada e inventada para
> este proyecto. No es documentación oficial de Umbraco ni refleja su formato real de intercambio.

## Estructura esperada de un nodo Umbraco (demostración)

Un nodo de contenido se representa con `key`, `culture`, `contentType` y `properties`. Los valores
editables (`title`, `bodyText`, `seoDescription`) viven dentro de `properties` y sus nombres usan
camelCase, a diferencia de la demostración de Sitecore, que usa nombres con mayúscula inicial.

## REGLA UM-001 — Propiedades obligatorias del nodo Umbraco
- aplicabilidad: obligatoria
- severidad: ERROR
- verificacion: esquema

El nodo debe incluir `key`, `culture`, `contentType` y `properties`. El objeto `properties` debe incluir
`title` y `bodyText`. `key`, `culture` y `contentType` son cadenas; `properties` es un objeto; `title` y
`bodyText` son cadenas.

```json-schema
{
  "type": "object",
  "required": ["key", "culture", "contentType", "properties"],
  "properties": {
    "key": {"type": "string"},
    "culture": {"type": "string"},
    "contentType": {"type": "string"},
    "properties": {
      "type": "object",
      "required": ["title", "bodyText"],
      "properties": {
        "title": {"type": "string"},
        "bodyText": {"type": "string"}
      }
    }
  }
}
```

## REGLA UM-002 — Formatos de key, culture y contentType en Umbraco
- aplicabilidad: obligatoria
- severidad: ERROR
- verificacion: esquema

`key` debe ser un GUID con formato 8-4-4-4-12. `culture` debe seguir el formato `xx-XX`. `contentType`
debe estar en camelCase (empieza con minúscula y no contiene espacios). `title` admite hasta 255
caracteres.

```json-schema
{
  "type": "object",
  "properties": {
    "key": {"type": "string", "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"},
    "culture": {"type": "string", "pattern": "^[a-z]{2}-[A-Z]{2}$"},
    "contentType": {"type": "string", "pattern": "^[a-z][A-Za-z0-9]*$"},
    "properties": {
      "type": "object",
      "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 255}
      }
    }
  }
}
```

## REGLA UM-003 — Propiedades permitidas en la raíz del nodo Umbraco
- aplicabilidad: obligatoria
- severidad: WARNING
- verificacion: esquema

En la raíz del nodo solo se admiten `key`, `culture`, `contentType`, `properties`, `variant` y
`parentKey`. Otras propiedades son inesperadas.

```json-schema
{
  "type": "object",
  "properties": {
    "key": {}, "culture": {}, "contentType": {}, "properties": {}, "variant": {}, "parentKey": {}
  },
  "additionalProperties": false
}
```

## REGLA UM-004 — seoDescription recomendada en Umbraco
- aplicabilidad: opcional
- severidad: WARNING
- verificacion: esquema

Se recomienda incluir `properties.seoDescription` (cadena de hasta 160 caracteres). Es opcional: si el
equipo decide no aplicarla, su ausencia no debe reportarse.

```json-schema
{
  "type": "object",
  "properties": {
    "properties": {
      "type": "object",
      "required": ["seoDescription"],
      "properties": {
        "seoDescription": {"type": "string", "maxLength": 160}
      }
    }
  }
}
```
