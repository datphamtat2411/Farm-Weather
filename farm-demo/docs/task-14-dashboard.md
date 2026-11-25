# Task 14 — Dashboard tổng quan

Native ThingsBoard tenant widget, configured in a single scoped dashboard using the existing security manifest. No new auth store, no public service key, no raw RPC or SQL. The widget queries FarmApi using the ThingsBoard HttpClient/JWT every 10 seconds; the read-only projection uses the same human JWT for native REST queries, not the service key. Data refresh follows measurement timestamps; periodic and event cursors are independent. Gateway status is last reported health and marked stale after 90 seconds. Radio diagnostics are derived from stored ingress metadata, explicitly labeled simulated when applicable. A missing receiver is unknown, not zero. Daily rain reports the known sum of complete valid non-overlapping periods within Vietnam local day and coverage; no midnight interpolation. Trend is 24 h, gaps are not joined; detailed statistics/CSV belong to Task 15.

After the Task 12 provisioning, install/update the native dashboard:
```sh
python farm-demo/cloud/dashboard.py --url https://localhost:8443 --ca-file /path/to/ca.crt --manifest /private/security.json
```
Use the printed dashboard URL under the deployment reverse proxy host, not the loopback address for a remote browser. Start FarmApi and use the existing nginx-farm.conf.example same-origin proxy. All four human roles can read; mutations remain controlled server-side. Native dashboard/widget install is idempotent, scoped to Farm dashboards; never assign an unrelated customer's resource. User-visible commands only show APPLIED from confirmed RPC status. On loss of UI connectivity controls are hidden and prior values labeled stale. No start pump command.

CI development checks native provisioning, scoped data, validity/freshness, controls and browser rendering. Wokwi full hardware acceptance remains Task 17. No claim of live public hosting or physical sensor measurement.

Browser evidence is captured from the actual native dashboard under the isolated ThingsBoard instance (not a mock page). The CI route forwards the existing same-origin FarmApi path to the real TLS service. Production uses nginx as before.
