# Integration configuration

## Generic REST official source

Institution Verifier → Official sources → **Add official source**. Configuration JSON:

```json
{
  "test_request": {"method": "GET", "path": "/health", "expect_status": 200, "expect_json_path": "status"},
  "request": {"method": "GET", "path": "/institutes/{aicte_permanent_id}", "query": {"year": "{academic_year}"}},
  "response": {"found_path": "data", "fields": {"institution_name": "data.name", "aicte_permanent_id": "data.permanentId"}},
  "match": {"institution_name": "name"}
}
```

Placeholders are extracted document fields (officer corrections applied). Outbound calls: HTTPS only, ports 443/8443, private/reserved addresses refused, redirects not followed, 20 s timeout, 10 MB cap. Credentials: environment variable named on the integration (recommended) or write-only encrypted storage. Any configuration change resets the status to "Not tested".

## Inspection models

Requests follow Roboflow's documented hosted API: `POST {base}/{project}/{version}?confidence=&overlap=&format=json`, base64 body, `Authorization: Bearer <key>` (set `"api_key_transport": "query"` in the provider config for the legacy `api_key` parameter). Parsed outputs: detection boxes (centre x/y, width, height), instance-segmentation `points` polygons, classification `predictions`/`top`. Class rules (`finding`, `severity` + `severity_basis`, `min_confidence`, `criterion_code`) decide which detections create findings.
