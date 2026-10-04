# DeleteWitness

A real finite SQLite profile/attachment application with logical tombstones,
durable deletion intents, shared-reference protection and concrete filesystem
receipts. Public SDK and registered CLI consume JSON profiles and actual binary
attachments. A deletion transaction clears the profile body, marks it deleted,
removes its references and records attachment work. Application reads reject the
tombstone immediately. Operator reconciliation unlinks only a matching owned
file with no remaining references and records UNLINKED, observed ABSENT or UNKNOWN.

Python3.11+, ordinary wheel, MIT, stdlib runtime:

    python -m venv .venv
    .venv/Scripts/python.exe -m pip wheel . --no-deps --wheel-dir dist
    .venv/Scripts/python.exe -m pip install --no-deps --find-links dist deletewitness
    .venv/Scripts/python.exe -I examples/workflow.py
    .venv/Scripts/python.exe -I -m unittest discover -s tests -v

On POSIX use .venv/bin/python. Context(tenant,operator=False) is an already
authenticated application context supplied by trusted backend code. It is NOT
an authentication verifier; CLI tenant/operator switches are for the trusted
local operator. Cross-tenant grant requires an authorized operator context.
Normal tenant operations filter the exact tenant and cannot link another
tenant's private attachment. No real personal datasets are used in examples.

Create an empty dedicated attachment directory (or let init create it) and a
new database OUTSIDE that directory. The directory and database namespace must
belong exclusively to this application. Existing foreign roots/databases,
symlink/junction ancestors, hardlinked managed files and database sidecar aliases
are refused. Stored blob paths are generated flat UUID names; no caller filename
or deletion-path argument is accepted. All cooperating application mutations,
reads, new references, replacement and reconciliation serialize via the same
SQLite BEGIN IMMEDIATE transaction. Replacement creates a new immutable blob
and atomically swaps the logical reference before old-file reconciliation.

    deletewitness --database app.sqlite --root attachments init
    deletewitness --database app.sqlite --root attachments --tenant demo create --input profile.json
    deletewitness --database app.sqlite --root attachments --tenant demo put --profile PROFILE_UUID --input attachment.bin
    deletewitness --database app.sqlite --root attachments --tenant demo get --profile PROFILE_UUID
    deletewitness --database app.sqlite --root attachments --tenant demo read --profile PROFILE_UUID --blob BLOB_UUID
    deletewitness --database app.sqlite --root attachments --tenant demo delete --profile PROFILE_UUID
    deletewitness --database app.sqlite --root attachments --tenant demo --operator reconcile
    deletewitness --database app.sqlite --root attachments --tenant demo receipt --request REQUEST_UUID
    deletewitness --database app.sqlite --root attachments --tenant demo --operator inventory

get/read intentionally return requested live profile/content; diagnostics and
deletion receipts contain no original profile JSON, attachment bytes, original
filenames or content digests. Opaque UUIDs, tenant identifiers, reference state,
file identity/length and observation times remain as operational metadata; this
does not certify deletion of every possible personal identifier. Use nonpersonal
tenant names. Deleting the last reference clears the stored content digest.
No content backup is copied into an audit ledger. Retained terminal tombstones,
blob metadata and receipts count toward fixed lifetime quotas; they are not
silently pruned to permit identity reuse. Their exhaustion refuses new work.

Empty attachments are supported and consume an identity/metadata row even with
zero byte budget. Profile canonical JSON≤8192UTF-8bytes; CLI source JSON≤65536.
An attachment≤1MiB, configurable tracked bytes≤64MiB, profile/blob/request counts
≤10000, at most256references per profile and reconciliation batch1–256. Limits
are immutable in the owned database. Tracked logical bytes include ready,
uploading and pending-deletion blobs, not erased blobs; they are not allocated
disk/RSS quotas. SQLite max_page_count bounds ordinary database growth separately.
Preparation, hashing, binary copies, journals and filesystem allocation add cost.

Filesystem unlink is NOT atomic with SQLite. After logical commit, pending work
survives process death. After unlink but before receipt commit, restart observes
the path absent and records that narrower fact. A changed inode/content metadata,
symlink/hardlink, access failure or unavailable storage stays UNKNOWN and is not
deleted as though still the original file. A staged upload uses durable metadata;
valid staged content for a still-live owner can recover; missing content is
reported; identity-unknown staged content is retained for operator review.
put/replace UNKNOWN requires inspecting inventory/profile before retrying; they
do not promise idempotent identity under a lost acknowledgement.

This requires a trusted host, installed package and cooperating schema/directory
writers. There is no atomic inode-conditional unlink portable across supported
OSs, so malicious/noncooperating path replacement BETWEEN validation and unlink
is outside the contract. Detected replacements remain UNKNOWN. Native SQLite
rollback journals live in this exclusive owned namespace; foreign WAL/SHM and
non-native journals are refused. Normal reads complete under the application
lock, but already opened OS handles, previously delivered bytes, backups, other
copies and hardware retain their own lifetimes. UNLINKED means the controlled
directory entry was removed at that observation, never physical destruction.

[SQLite secure_delete](https://www.sqlite.org/pragma.html#pragma_secure_delete)
is already a database primitive; this application enables it for ordinary table
updates. Its documented virtual/shadow-table and storage limitations prevent a
physical-erasure guarantee. [S3 delete markers](https://docs.aws.amazon.com/AmazonS3/latest/userguide/DeleteMarker.html)
are existing logical deletion behavior and do not erase all object versions.
Official current sources checked2026-10-04UTC. Tombstones, intent queues and
reference counting are prior art, not a novel global deletion theorem.

No GDPR certification, SSD/backup erasure, remote S3 adapter, arbitrary SQL/schema,
untrusted filesystem defense or cross-resource atomicity is claimed. Customers,
paid demand, production adoption and revenue are unknown. Proposed value must be
judged against correct SQL-only/unlink-only/no-attachment controls and actual
crash-recovery consumers. See DESIGN/PILOT/ITERATIONS/SELF_REVIEW in docs.
