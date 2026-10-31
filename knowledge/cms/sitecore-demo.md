---
doc_id: sitecore-demo
titulo: Requisitos de importación de contenido para Sitecore (versión simplificada de demostración)
cms: sitecore
origen: demostracion_educativa
---

> **Documento de demostración con fines educativos.** Describe una estructura simplificada e inventada para
> este proyecto. No es documentación oficial de Sitecore ni refleja su formato de serialización real.

## Estructura esperada de un ítem Sitecore (demostración)

Un ítem de contenido se representa como un objeto con las propiedades `itemId`, `language`, `template` y
`fields`. Los campos de contenido editable (`Title`, `Body`, `MetaDescription`) viven dentro de `fields` y
sus nombres comienzan con mayúscula.

## REGLA SC-001 — Propiedades obligatorias del ítem Sitecore
- aplicabilidad: obligatoria
- severidad: ERROR
- verificacion: esquema

El ítem debe incluir `itemId`, `language`, `template` y `fields`. El objeto `fields` debe incluir `Title`
y `Body`. Los tipos son: `itemId`, `language` y `template` son cadenas; `fields` es un objeto; `Title` y
`Body` son cadenas.

```json-schema
{
  "type": "object",
  "required": ["itemId", "language", "template", "fields"],
  "properties": {
    "itemId": {"type": "string"},
    "language": {"type": "string"},
    "template": {"type": "string"},
    "fields": {
      "type": "object",
      "required": ["Title", "Body"],
      "properties": {
        "Title": {"type": "string"},
        "Body": {"type": "string"}
      }
    }
  }
}
```

## REGLA SC-002 — Formatos de itemId, language y Title en Sitecore
- aplicabilidad: obligatoria
- severidad: ERROR
- verificacion: esquema

`itemId` debe ser un GUID con formato 8-4-4-4-12 en hexadecimal. `language` debe seguir el formato
`xx-XX` (por ejemplo `es-AR`). `Title` no puede estar vacío y tiene un máximo de 150 caracteres.

```json-schema
{
  "type": "object",
  "properties": {
    "itemId": {"type": "string", "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"},
    "language": {"type": "string", "pattern": "^[a-z]{2}-[A-Z]{2}$"},
    "fields": {
      "type": "object",
      "properties": {
        "Title": {"type": "string", "minLength": 1, "maxLength": 150}
      }
    }
  }
}
```

## REGLA SC-003 — Propiedades permitidas en la raíz del ítem Sitecore
- aplicabilidad: obligatoria
- severidad: WARNING
- verificacion: esquema

En la raíz del ítem solo se admiten `itemId`, `language`, `template`, `fields`, `version` y `path`.
Cualquier otra propiedad en la raíz es inesperada y puede ser ignorada o rechazada por el importador.

```json-schema
{
  "type": "object",
  "properties": {
    "itemId": {}, "language": {}, "template": {}, "fields": {}, "version": {}, "path": {}
  },
  "additionalProperties": false
}
```

## REGLA SC-004 — MetaDescription recomendada en Sitecore
- aplicabilidad: opcional
- severidad: WARNING
- verificacion: esquema

Se recomienda incluir `fields.MetaDescription` (cadena de hasta 160 caracteres) para mejorar el
posicionamiento en buscadores. Es opcional: si el equipo decide no aplicarla, su ausencia no debe
reportarse.

```json-schema
{
  "type": "object",
  "properties": {
    "fields": {
      "type": "object",
      "required": ["MetaDescription"],
      "properties": {
        "MetaDescription": {"type": "string", "maxLength": 160}
      }
    }
  }
}
```
