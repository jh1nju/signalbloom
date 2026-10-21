# SignalBloom Agent API (v1)

The local agent uses this API to send network data to the SignalBloom cloud app and to collect approved fixes.

## Rules

- HTTPS only. Base URL: `https://<cloud-run-url>/api`
- The agent always calls the cloud. The cloud never calls the agent, and the agent never opens a port.
- All bodies are JSON. All times are ISO 8601 with a time zone, e.g. `2026-11-03T14:30:00+13:00`.
- After registration, every call sends `Authorization: Bearer <site_token>`.
- Site tokens are stored hashed in the database and never written to logs.
- Errors always use this shape:

```json
{ "error": { "code": "invalid_token", "message": "Site token is missing or not valid." } }
```

## 1. Register the agent

`POST /api/agent/register` (no token; uses a setup code)

An Admin creates a site in Account & Settings, which shows a one-time setup code. Codes work once and expire after 24 hours.

Request:

```json
{
  "setup_code": "SB-7KQ2-91XD",
  "agent_version": "0.1.0",
  "hostname": "clinic-laptop-01"
}
```

Response `201 Created`:

```json
{
  "site_id": "site_8f3a2c",
  "site_token": "sbt_live_4e9b...",
  "interval_seconds": 30,
  "bandwidth_interval_seconds": 900
}
```

The agent saves `site_id` and `site_token` locally. The token is shown only in this response.

Errors: `400 invalid_body`, `404 code_not_found`, `410 code_used_or_expired`.

## 2. Send readings

`POST /api/ingest`

Sent every 30 seconds. `readings` is a list so the agent can resend readings it stored while the cloud was unreachable (up to 500 per request).

Request:

```json
{
  "site_id": "site_8f3a2c",
  "sent_at": "2026-11-03T14:30:00+13:00",
  "readings": [
    {
      "measured_at": "2026-11-03T14:30:00+13:00",
      "latency_ms": 24.5,
      "packet_loss_pct": 0.0,
      "internet_up": true,
      "download_mbps": null,
      "upload_mbps": null,
      "error_rate_pct": null
    }
  ],
  "devices": [
    {
      "mac": "aa:bb:cc:dd:ee:01",
      "ip": "192.168.1.20",
      "hostname": "reception-pc",
      "vendor": "Dell",
      "online": true
    }
  ]
}
```

- `download_mbps` and `upload_mbps` are filled only when the 15-minute bandwidth test runs; otherwise `null`.
- `error_rate_pct` is `null` unless the router reports it.
- The cloud ignores a reading it already has for the same site and `measured_at`, so resending is safe.

Response `202 Accepted`:

```json
{ "accepted": 1, "duplicates": 0 }
```

Errors: `400 invalid_body`, `401 invalid_token`, `403 site_mismatch` (token belongs to another site), `413 too_many_readings`.

## 3. Check for approved fixes

`GET /api/agent/commands`

Called every 30 seconds. Returns fixes an Admin has approved that the agent has not collected yet. Once returned, a command is marked as sent.

Response `200 OK`:

```json
{
  "commands": [
    {
      "id": "cmd_51d0",
      "type": "restart_device",
      "target": { "mac": "aa:bb:cc:dd:ee:01" },
      "approved_at": "2026-11-03T14:31:10+13:00",
      "simulated": true
    }
  ]
}
```

Command types: `restart_device`, `isolate_device`, `reconnect_device`. All are simulated in the prototype. An empty list means nothing is waiting.

Errors: `401 invalid_token`.

## 4. Report a fix result

`POST /api/agent/commands/{id}/result`

Request:

```json
{
  "status": "succeeded",
  "finished_at": "2026-11-03T14:31:42+13:00",
  "message": "Simulated restart of reception-pc completed."
}
```

`status` is `succeeded` or `failed`. The result appears in the dashboard and is written to the audit log.

Response `200 OK`:

```json
{ "recorded": true }
```

Errors: `401 invalid_token`, `404 command_not_found`, `409 result_already_recorded`.

## Changing this API

Any change needs team agreement, an update to this file, and a version bump (v1 to v2). The agent and the cloud app must change together.
