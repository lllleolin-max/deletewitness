# Predeclared native application controls

Same synthetic JSON profile/binary attachment fixture and mutation/kill schedule
compare correct SQL-only logical deletion, file unlink-only and durable tombstone/
intent/reconciliation. Independent native SQL and filesystem consumers check
remaining readable profile bodies, references and every complete attachment byte
for unaffected users; opaque receipts are checked against observed paths.
Shared cross-tenant reference scenario preserves other users, deletion-only
files are actually unlinked, changed identities produce UNKNOWN. No-attachment
fixture lets simple SQL be equally correct and cheaper; do not hide that case.

Kill actual ordinary-installed process after logical commit/before unlink and
after unlink/before receipt commit. Product recovery precedes any native SQLite
oracle so the oracle cannot accidentally perform the hot-journal recovery for
the application. Record fixture setup, ordinary operations, actual recovered
items, manually unresolved residuals, content/path/ref outcomes, all byte/count
limits, full outputs and wall time; no inferred customer revenue/labor savings.
Controls may provide different consistency semantics; disclose exactly which
real consumer rejects/accepts a tombstone rather than claiming a weaker baseline
was a mature equivalent. Current execution and independent value scoring pending.

## First actual whole observation

Code9b06eb5d90bec1d4b1f95e2d511d29a4e29799d5; canonical ordinary fresh
Windows3.14, UTC2026-10-04T22:59:34.350638–22:59:36.891868. Thirteen actual
SQLite/filesystem cases passed all native logical/body/ref/shared checks and
complete unaffected binary consumers. Four real processes were killed (each
of3controls after its primary resource phase, plus durable after unlink/before
receipt). SQL-only and durable primary phase is logical commit; reference-aware
unlink-only phase is its file work, with SQL untouched. No equivalent cross-
resource consistency or fake common filesystem/receipt checkpoint is claimed.

| Fixture | Control | Target profile readable | Exclusive leftover files | Work milliseconds | Reconcile calls |
|---|---|---|---:|---:|---:|
| Ordinary |SQL-only|no|2|3.978|0|
| Ordinary |reference-aware unlink-only|yes|0|2.089|0|
| Ordinary |durable intent|no|0|24.171|2|
| Shared |SQL-only|no|1|5.413|0|
| Shared |reference-aware unlink-only|yes|0|3.464|0|
| Shared |durable intent|no|0|23.859|1|
| No attachments |SQL-only|no|0|3.168|0|
| No attachments |unlink-only|yes|0|1.762|0|
| No attachments |durable intent|no|0|14.134|1|
| Primary phase kill |SQL-only|no|2|213.614|0|
| Primary phase kill |reference-aware unlink-only|yes|0|200.961|0|
| Primary phase kill |durable intent|no|0|261.899|2|
| Unlink before receipt kill |durable intent|no|0|271.816|2|

Every unaffected attachment, including cross-tenant shared content, matched its
complete independent input bytes/hash. Native correct SQL-only already gives
the required logical outcome in the no-attachment fixture and costs LESS;
durable machinery provides no needed filesystem benefit there. It also costs
more in other fixtures. Its demonstrated added output is the recoverable file
work and specific receipts. Native narrower controls are useful primitives,
not broken rivals to mature deletion platforms; no customer/labor/time savings
are inferred. Complete paths/refs/contents/receipts, actual PID/kill codes and
all raw setup/operation values remain retained.

Each fresh fixture/setup0.04476–0.11499s. SQLite native page_count*page_size
observed65536bytes in every case; files report logical lengths separately,
not physical allocation or RSS. Costs include real process startup/recovery for
kill fixtures. Single observations are not a performance significance claim.

The original pilot scope sentence accidentally said5kills; its actual stdout,
four child readiness/exit receipts and13case records say4. That reporting error
is retained and the successor sentence corrected, with no product/oracle change
or extra repair round. Reproduce ordinary installed: python -I probes/pilot.py
--output NEW_DIRECTORY. Independent value scoring remains pending.

Additional unchanged resource_safety.py against runtimef1795ac actually consumed
full1048576byte input/output SHA256
fbbab289f7f94b25736c58be46a994c441fd02552cc6022352e3d86d2fab7c83,
refused input+1/tracked±1, preserved actual hardlinks/foreign WAL/database alias,
and privately rejected damaged marker/DB/missing root. Native symlink creation
was NOT_EXECUTED on this Windows host (privilege error1314); do not claim it ran.
The implementation guard remains part of the original support boundary, with
independent/Ubuntu actual symlink validation still required.
