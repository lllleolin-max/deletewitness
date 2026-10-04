# Protocol and support boundary

The fixed application schema is settings/profiles/blobs/refs/requests/items.
Profiles and tenant-scoped references are the real consumer data. Tombstone,
body clearing, reference removal and deletion work commit in one SQLite write
transaction with secure_delete enabled and synchronous EXTRA. Every public
consumer starts the same lock and checks profile.deleted; no SQL-only reader
ignoring that flag is asserted to enforce the application contract.

Each blob is an immutable generated flat filename. A durable uploading row is
allocated before file creation so an interrupted upload remains bounded and
diagnosable. Creation and reference publication happen while holding the same
database writer lock; replacement swaps references in that transaction. An
allocated upload whose owner was deleted cannot create a fresh file later.
Upload allocation durably records put vs replace and its exact old blob target.
Recovery publishes intact staged content for an active owner, report absent
staging or preserve UNKNOWN. Partial/identity-unknown staging is not silently
deleted or called a successful upload.

Replacement recovery requires the old reference still belong to that active
profile, checks removal-receipt budget and swaps both references/creates old
deletion intent in the same transaction. A concurrently changed old reference
stays UNKNOWN. Additive owned-schema migration preserves old active data and
pending deletion work; old interrupted uploads with no recorded put/replace
instruction remain UNKNOWN because their original operation cannot be recovered
from bytes alone. No such ambiguity is invented into a successful replacement.

Last-reference removal changes the blob to deleting and clears its content
digest; retained deletion items contain no payload/digest/original filename.
Shared references make a terminal SHARED observation for that request. Later
last-reference deletion has its own work and receipt; a SHARED receipt records
that historical protection, not a permanent promise the file still exists.

Receipts record the actual removed reference count separately from cancelled
staged-upload allocations. The latter may have no published reference or file.
Both are committed with the tombstone; they are not inferred from work-item
count. Old receipts lacking that distinction expose counts_exact=false,
removed_references=null and legacy_unverified_candidates; migration cannot
reconstruct lost observations and does not silently relabel them as exact.

POSIX identity uses native inode/device/size/mtime/ctime. Windows uses an open
handle's native FILE_BASIC_INFO ChangeTime consistently; Python's historical
Windows ctime can represent birth time for path stat and different time for a
handle. The [Microsoft structure](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_basic_info)
and [GetFileInformationByHandleEx](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getfileinformationbyhandleex)
are existing OS APIs checked2026-10-04UTC, not a new primitive. Handle and path
identity plus complete read digest are checked before/after content delivery.

Reconciliation holds BEGIN IMMEDIATE while checking zero refs, lstat identity,
link count/regular-file status and unlink. It commits a concrete path observation
afterwards. Crash after intent commit is recoverable; crash after unlink rolls
back only the DB observation, so retry records ABSENT. This is deliberately
weaker than proving who removed it or physical data destruction. New cooperating
references to deleting/gone blobs refuse. File changes, aliases and I/O faults
yield UNKNOWN, preserving the ambiguous current path. Bounded batches rotate
using a durable least-recent-attempt logical sequence, then UUID tie, so an
UNKNOWN cannot monopolize every batch ahead of other finite pending intents.
Each committed attempt advances sequence; a killed transaction may roll it
back and retry, consistent with the still-pending effect observation. No timing
or immediate completion promise is made for genuinely unavailable files.

The database lives outside the owned attachment directory, with reserved native
SQLite main/-journal/-wal/-shm names. Reopen validates ordinary single-link main
file header role before SQLite opens it, owned root marker/namespace and root
identity, then permits the owned native DELETE rollback journal to recover via
RW BEGIN IMMEDIATE. WAL/SHM and non-native journals are refused. A valid-looking
journal forged by a malicious host is outside this trusted namespace contract.
Foreign sidecars and symlink/junction/hardlink aliases are not adopted or erased.
Host/schema/direct-file tampering is not cryptographically prevented; unavailable
or damaged state returns controlled refusal/UNKNOWN rather than inventing proof.

Native filesystem replacement cannot be conditionally unlinked by inode across
all supported OSs. The supported guarantee serializes cooperative writers and
detects prior replacements; malicious concurrent modification during the check/
unlink interval is excluded FROM THE INITIAL CONTRACT. Already opened native
readers or previously returned bytes cannot be recalled. Windows open handles/
readonly files can prevent unlink and then produce UNKNOWN; POSIX unlink can
remove a name while an earlier handle remains usable. Both receipts assert only
the recorded owned-path observation.

Logical tracked bytes include staging/ready/deleting until observed absent.
Count/byte limits are bounded, immutable and reject before further allocation.
Lifetime tombstones/receipts remain in count budgets; no identity/receipt reuse.
SQLite page limit is8192pages at default4096byte pages, separately from tracked
attachment bytes; journals, serialization, filesystem allocation and memory add
cost. No hard RSS, secure storage hardware or power-failure certification.

Context is an authorization result from trusted application code. Normal exact
tenant filtering and same-tenant reference rights remain in SDK; global reconcile,
inventory and cross-tenant grant require trusted operator role. Local CLI can
set that role only because its caller is the trusted local administrator.
The checkpoint callback is a trusted laboratory observation hook with phase/
opaque ID only; actual process-kill probes use it, not pretend exceptions as kill.
