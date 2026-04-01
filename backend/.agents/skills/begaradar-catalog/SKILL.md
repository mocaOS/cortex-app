---
name: begaradar-catalog
description: >
  Query the BeGaRadar Service Catalog API to retrieve all service catalog entries
  including responsible contacts (business, IT, 3rd-level), companies, manufacturers,
  monitoring services, and system connections. Use when asked about the service catalog,
  application landscape, system overview, responsible persons, IT contacts, or
  dependencies between systems at BEGA.
compatibility: Requires curl and a valid BeGaRadar API token
metadata:
  author: bega
  version: "1.0"
---

## What this skill does

Queries the BeGaRadar Service Catalog API and returns structured JSON data about all
registered systems, applications, and services including their responsible contacts
and interconnections.

## API Endpoint

```
GET /api/service-catalog
```

**Base URL:** `https://radar.bega-apps.de` (production)

## Authentication

The API requires a Bearer token in the `Authorization` header.
The token is managed in the BeGaRadar backend under **Einstellungen > API**.

## How to call the API

```bash
curl -s -H "Authorization: Bearer API_TOKEN" https://radar.bega-apps.de/api/service-catalog
```

Replace `API_TOKEN` with the actual token. If you don't have the token, ask the user to provide it or to check Einstellungen > API in BeGaRadar.

## Response format

The API returns JSON:

```json
{
  "success": true,
  "count": 12,
  "data": [ ... ]
}
```

`data` is an array of catalog entry objects.

## Catalog entry fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique ID |
| `name` | string | System/application name |
| `description` | string or null | Free-text description |
| `icon` | string or null | FontAwesome icon class (e.g. `"fas fa-server"`) |
| `frontend_url` | string or null | URL to the web application |
| `is_active` | boolean | Whether the entry is active |
| `is_external` | boolean | Whether this is an external application (not internally operated) |
| `sort_order` | integer | Sort position (ascending) |
| `parent` | object or null | Parent entry (`{id, name}`) for sub-systems |
| `children` | array | Child entries (`[{id, name}, ...]`) |
| `responsible_business` | contact or null | Business/operations contact (internal BEGA person) |
| `responsible_it` | contact or null | IT contact or external software company |
| `responsible_3rd` | contact or null | 3rd-level escalation contact |
| `manufacturer` | company or null | Software manufacturer/vendor |
| `services` | array | Linked monitoring services (`[{id, name}, ...]`) |
| `connections_from` | array | Outgoing connections to other entries |
| `connections_to` | array | Incoming connections from other entries |

## Contact object

Contacts are polymorphic. Check the `type` field to determine the structure.

**When type = "user" (a person):**

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"user"` |
| `id` | integer | User ID |
| `name` | string | Full name (first + last) |
| `email` | string | Email address |
| `phone` | string or null | Phone number |
| `company` | company or null | The user's company |

**When type = "company" (a company directly):**

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"company"` |
| `id` | integer | Company ID |
| `name` | string | Company name |
| `phone` | string or null | Phone number |
| `email` | string or null | Email address |

## Company object (manufacturer or user's company)

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Company ID |
| `name` | string | Company name |
| `phone` | string or null | Phone number |
| `email` | string or null | Email address |

## Connection object

**connections_from** (this entry -> other entry):

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Connection ID |
| `to_entry_id` | integer | Target entry ID |
| `label` | string or null | Connection label (e.g. "REST API", "SFTP") |

**connections_to** (other entry -> this entry):

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Connection ID |
| `from_entry_id` | integer | Source entry ID |
| `label` | string or null | Connection label |

## Contact roles explained

| Role | Field | Who is this? |
|------|-------|-------------|
| **Betrieb (Business)** | `responsible_business` | Internal BEGA contact responsible for day-to-day operations |
| **IT** | `responsible_it` | Technical contact or external dev company maintaining the system |
| **3rd-Level** | `responsible_3rd` | Escalation contact for deep technical issues (e.g. vendor support) |
| **Hersteller (Manufacturer)** | `manufacturer` | The company that built/develops the software |

## Error responses

| HTTP Code | Meaning |
|-----------|---------|
| 200 | Success |
| 401 | `{"error": "API-Token fehlt"}` or `{"error": "Ungültiger API-Token"}` |
| 500 | Server error |

## Example: Find all systems and their IT contacts

```bash
curl -s -H "Authorization: Bearer API_TOKEN" https://radar.bega-apps.de/api/service-catalog | jq '.data[] | {name, it_contact: .responsible_it.name, it_company: (.responsible_it.company.name // .responsible_it.name // "N/A")}'
```

## Example: List all connections between systems

```bash
curl -s -H "Authorization: Bearer API_TOKEN" https://radar.bega-apps.de/api/service-catalog | jq '.data[] | select(.connections_from | length > 0) | {name, connects_to: [.connections_from[] | {target: .to_entry_id, via: .label}]}'
```
