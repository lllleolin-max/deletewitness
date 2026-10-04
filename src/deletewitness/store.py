"""Durable application deletion protocol. Filesystem effects are NOT a DB txn."""
from contextlib import contextmanager
from dataclasses import asdict
import json,os,sqlite3,time
from pathlib import Path
from . import files
from .model import APP_ID,VERSION,MAX_REFS,Context,Limits,WitnessError,decode,encode,fail,identity,integer,new_id,profile

SCHEMA='''
CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE profiles(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,body BLOB,deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN(0,1)),delete_request TEXT);
CREATE TABLE blobs(id TEXT PRIMARY KEY,size INTEGER NOT NULL CHECK(size>=0),digest TEXT,identity TEXT,state TEXT NOT NULL CHECK(state IN('uploading','ready','deleting','gone')),staged_for TEXT,upload_mode TEXT NOT NULL DEFAULT 'legacy' CHECK(upload_mode IN('legacy','put','replace')),replace_of TEXT,work_seq INTEGER NOT NULL DEFAULT 0 CHECK(work_seq>=0));
CREATE TABLE refs(tenant TEXT NOT NULL,profile TEXT NOT NULL REFERENCES profiles(id),blob TEXT NOT NULL REFERENCES blobs(id),PRIMARY KEY(profile,blob));
CREATE TABLE requests(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,profile TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN('delete','replace')),removed INTEGER NOT NULL,created_ns INTEGER NOT NULL,cancelled_uploads INTEGER);
CREATE TABLE items(request TEXT NOT NULL REFERENCES requests(id),blob TEXT NOT NULL REFERENCES blobs(id),status TEXT NOT NULL CHECK(status IN('PENDING','SHARED','UNLINKED','ABSENT','UNKNOWN')),reason TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,observed_ns INTEGER,PRIMARY KEY(request,blob));
CREATE INDEX refs_blob ON refs(blob);
CREATE INDEX items_pending ON items(status,blob);
CREATE INDEX blobs_work ON blobs(state,work_seq,id);
'''
TABLES={'settings','profiles','blobs','refs','requests','items'}

class Store:
    def __init__(self,database,root,*,checkpoint=None):
        self.database=files.absolute(database);self.root=files.absolute(root)
        self.checkpoint=checkpoint or (lambda phase,identifier:None)
        self._preflight()
        with self._tx() as c:
            try:self.limits=Limits(**json.loads(self._setting(c,'limits')))
            except (TypeError,ValueError):fail('invalid_schema')

    @classmethod
    def initialize(cls,database,root,limits=Limits()):
        if not isinstance(limits,Limits):fail('invalid_limits')
        db=files.absolute(database);directory=files.absolute(root)
        files.namespaces(db,directory)
        if any(Path(str(db)+suffix).exists() for suffix in files.SIDES):fail('database_exists')
        if not db.parent.is_dir():fail('database_parent_missing')
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):fail('root_not_empty')
        try:
            if not directory.exists():directory.mkdir()
            root_info=directory.stat();namespace=new_id()
            marker=dict(namespace=namespace,database=str(db),dev=root_info.st_dev,ino=root_info.st_ino)
            files.create_file(directory/files.MARKER,encode(marker))
            fd=os.open(db,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
            c=sqlite3.connect(db,isolation_level=None)
            try:
                c.execute('PRAGMA secure_delete=ON');c.execute('PRAGMA synchronous=EXTRA')
                c.execute('PRAGMA max_page_count=8192');c.execute('PRAGMA application_id='+str(APP_ID));c.execute('PRAGMA user_version='+str(VERSION))
                c.executescript(SCHEMA)
                c.execute('BEGIN IMMEDIATE')
                values={'namespace':namespace,'root':str(directory),'root_identity':json.dumps(dict(dev=root_info.st_dev,ino=root_info.st_ino)),'limits':json.dumps(asdict(limits)),'work_sequence':'0'}
                c.executemany('INSERT INTO settings VALUES(?,?)',values.items());c.commit()
            finally:c.close()
        except (OSError,sqlite3.Error):fail('initialization_unknown')
        return cls(db,directory)

    def _preflight(self):
        try:self._preflight_files()
        except OSError:fail('path_unknown')

    def _preflight_files(self):
        if files.absolute(self.database)!=self.database or files.absolute(self.root)!=self.root:fail('path_alias')
        files.namespaces(self.database,self.root)
        if not self.database.exists():fail('database_missing')
        files.owned_header(self.database)
        if not self.root.is_dir():fail('root_missing')
        self.marker=files.read_marker(self.root)
        info=self.root.stat()
        if self.marker.get('database')!=str(self.database) or self.marker.get('dev')!=info.st_dev or self.marker.get('ino')!=info.st_ino:fail('foreign_root')
        identity(self.marker.get('namespace'))

    @staticmethod
    def _setting(c,key):
        row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        if not row:fail('invalid_schema')
        return row[0]

    @contextmanager
    def _tx(self):
        self._preflight();c=None
        try:
            c=sqlite3.connect(self.database,timeout=1,isolation_level=None);c.row_factory=sqlite3.Row
            c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA secure_delete=ON');c.execute('PRAGMA synchronous=EXTRA')
            # This limit is connection-local. Existing oversized stores cannot
            # be made compliant by setting it: SQLite returns their current size.
            if c.execute('PRAGMA max_page_count=8192').fetchone()[0]!=8192:fail('database_over_page_quota')
            c.execute('BEGIN IMMEDIATE')
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables!=TABLES or self._setting(c,'namespace')!=self.marker['namespace'] or self._setting(c,'root')!=str(self.root):fail('invalid_schema')
            columns={r[1] for r in c.execute('PRAGMA table_info(blobs)')}
            if 'upload_mode' not in columns:c.execute("ALTER TABLE blobs ADD COLUMN upload_mode TEXT NOT NULL DEFAULT 'legacy' CHECK(upload_mode IN('legacy','put','replace'))")
            if 'replace_of' not in columns:c.execute('ALTER TABLE blobs ADD COLUMN replace_of TEXT')
            if 'work_seq' not in columns:c.execute('ALTER TABLE blobs ADD COLUMN work_seq INTEGER NOT NULL DEFAULT 0 CHECK(work_seq>=0)')
            c.execute('CREATE INDEX IF NOT EXISTS blobs_work ON blobs(state,work_seq,id)')
            c.execute("INSERT OR IGNORE INTO settings VALUES('work_sequence','0')")
            request_columns={r[1] for r in c.execute('PRAGMA table_info(requests)')}
            if 'cancelled_uploads' not in request_columns:c.execute('ALTER TABLE requests ADD COLUMN cancelled_uploads INTEGER')
            yield c;c.commit()
        except sqlite3.Error as error:
            if c is not None:c.rollback()
            if getattr(error,'sqlite_errorcode',None)==sqlite3.SQLITE_FULL:fail('storage_full')
            fail('storage_unknown')
        except BaseException:
            if c is not None:c.rollback()
            raise
        finally:
            if c is not None:c.close()

    @staticmethod
    def _context(ctx):
        if not isinstance(ctx,Context):fail('invalid_context')
        return ctx.tenant

    def _active(self,c,ctx,pid):
        tenant=self._context(ctx);identity(pid)
        row=c.execute('SELECT * FROM profiles WHERE id=? AND tenant=? COLLATE BINARY',(pid,tenant)).fetchone()
        if row is None:fail('profile_missing')
        if row['deleted']:fail('profile_deleted')
        return row

    @staticmethod
    def _quota(c,table,cap):
        if c.execute('SELECT count(*) FROM '+table).fetchone()[0]>=cap:fail(table+'_quota')

    def create_profile(self,ctx,value):
        tenant=self._context(ctx);raw=profile(value);pid=new_id()
        with self._tx() as c:
            self._quota(c,'profiles',self.limits.profiles)
            c.execute('INSERT INTO profiles(id,tenant,body) VALUES(?,?,?)',(pid,tenant,raw))
        return pid

    def get_profile(self,ctx,pid):
        with self._tx() as c:
            row=self._active(c,ctx,pid)
            return dict(id=pid,tenant=ctx.tenant,profile=decode(row['body']),attachments=[r[0] for r in c.execute('SELECT blob FROM refs WHERE profile=? ORDER BY blob',(pid,))])

    def _new_request(self,c,ctx,pid,kind,removed,cancelled_uploads=0):
        self._quota(c,'requests',self.limits.requests);request=new_id()
        c.execute('INSERT INTO requests(id,tenant,profile,kind,removed,created_ns,cancelled_uploads) VALUES(?,?,?,?,?,?,?)',(request,ctx.tenant,pid,kind,removed,time.time_ns(),cancelled_uploads))
        return request

    def _item(self,c,request,blob):
        refs=c.execute('SELECT count(*) FROM refs WHERE blob=?',(blob,)).fetchone()[0]
        if refs:
            c.execute('INSERT INTO items(request,blob,status,reason,observed_ns) VALUES(?,?,?,?,?)',(request,blob,'SHARED','live_reference',time.time_ns()))
        else:
            c.execute("UPDATE blobs SET state='deleting',digest=NULL,staged_for=NULL WHERE id=?",(blob,))
            c.execute("INSERT INTO items(request,blob,status,reason) VALUES(?,?,'PENDING','durable_intent')",(request,blob))

    def put_attachment(self,ctx,pid,raw,*,replace=None):
        if not isinstance(raw,bytes):fail('invalid_attachment')
        if len(raw)>self.limits.blob_bytes:fail('attachment_size')
        if replace is not None:identity(replace)
        blob=new_id()
        with self._tx() as c:
            self._active(c,ctx,pid);self._quota(c,'blobs',self.limits.blobs)
            if replace is None and c.execute('SELECT count(*) FROM refs WHERE profile=?',(pid,)).fetchone()[0]>=MAX_REFS:fail('references_quota')
            if replace is not None:
                if not c.execute('SELECT 1 FROM refs WHERE profile=? AND blob=?',(pid,replace)).fetchone():fail('reference_missing')
                self._quota(c,'requests',self.limits.requests)
            tracked=c.execute("SELECT coalesce(sum(size),0) FROM blobs WHERE state!='gone'").fetchone()[0]
            if tracked+len(raw)>self.limits.tracked_bytes:fail('tracked_bytes_quota')
            c.execute("INSERT INTO blobs(id,size,digest,identity,state,staged_for,upload_mode,replace_of) VALUES(?,?,?,NULL,'uploading',?,?,?)",(blob,len(raw),files.digest(raw),pid,'replace' if replace is not None else 'put',replace))
        self.checkpoint('upload_allocated',blob)
        try:
            with self._tx() as c:
                self._active(c,ctx,pid)
                row=c.execute('SELECT * FROM blobs WHERE id=?',(blob,)).fetchone()
                if row['state']!='uploading':fail('upload_cancelled')
                if replace is None and c.execute('SELECT count(*) FROM refs WHERE profile=?',(pid,)).fetchone()[0]>=MAX_REFS:fail('references_quota')
                if replace is not None and not c.execute('SELECT 1 FROM refs WHERE profile=? AND blob=?',(pid,replace)).fetchone():fail('reference_missing')
                if replace is not None:self._quota(c,'requests',self.limits.requests)
                expected=files.create_file(files.blob_path(self.root,blob),raw)
                self.checkpoint('upload_created',blob)
                c.execute("UPDATE blobs SET identity=?,state='ready',staged_for=NULL WHERE id=?",(json.dumps(expected),blob))
                if replace is not None:
                    request=self._new_request(c,ctx,pid,'replace',1)
                    c.execute('DELETE FROM refs WHERE profile=? AND blob=?',(pid,replace));self._item(c,request,replace)
                c.execute('INSERT INTO refs VALUES(?,?,?)',(ctx.tenant,pid,blob))
        except OSError:fail('upload_unknown')
        return blob

    def link_attachment(self,ctx,pid,blob):
        identity(blob)
        with self._tx() as c:
            self._active(c,ctx,pid)
            authorized=c.execute('SELECT 1 FROM refs JOIN profiles ON refs.profile=profiles.id WHERE refs.blob=? AND refs.tenant=? COLLATE BINARY AND profiles.deleted=0',(blob,ctx.tenant)).fetchone()
            if not authorized:fail('attachment_missing')
            self._link(c,ctx.tenant,pid,blob)

    def grant_attachment(self,ctx,source_profile,blob,target_ctx,target_profile):
        self._context(ctx);self._context(target_ctx);identity(blob)
        if not ctx.operator:fail('operator_required')
        with self._tx() as c:
            self._active(c,ctx,source_profile);self._active(c,target_ctx,target_profile)
            if not c.execute('SELECT 1 FROM refs WHERE profile=? AND blob=?',(source_profile,blob)).fetchone():fail('reference_missing')
            self._link(c,target_ctx.tenant,target_profile,blob)

    @staticmethod
    def _link(c,tenant,pid,blob):
        row=c.execute('SELECT state FROM blobs WHERE id=?',(blob,)).fetchone()
        if row is None or row[0]!='ready':fail('attachment_unavailable')
        if c.execute('SELECT 1 FROM refs WHERE profile=? AND blob=?',(pid,blob)).fetchone():return
        if c.execute('SELECT count(*) FROM refs WHERE profile=?',(pid,)).fetchone()[0]>=MAX_REFS:fail('references_quota')
        c.execute('INSERT INTO refs VALUES(?,?,?)',(tenant,pid,blob))

    def read_attachment(self,ctx,pid,blob):
        identity(blob)
        with self._tx() as c:
            self._active(c,ctx,pid)
            if not c.execute('SELECT 1 FROM refs WHERE profile=? AND blob=?',(pid,blob)).fetchone():fail('reference_missing')
            row=c.execute('SELECT * FROM blobs WHERE id=?',(blob,)).fetchone()
            if row['state']!='ready' or not row['identity'] or not row['digest']:fail('attachment_unavailable')
            try:return files.read_checked(files.blob_path(self.root,blob),json.loads(row['identity']),row['digest'],self.limits.blob_bytes)
            except (OSError,ValueError):fail('file_unknown')

    def delete_profile(self,ctx,pid):
        self._context(ctx);identity(pid)
        with self._tx() as c:
            row=c.execute('SELECT * FROM profiles WHERE id=? AND tenant=? COLLATE BINARY',(pid,ctx.tenant)).fetchone()
            if row is None:fail('profile_missing')
            if row['deleted']:request=row['delete_request']
            else:
                references={r[0] for r in c.execute('SELECT blob FROM refs WHERE profile=?',(pid,))}
                uploads={r[0] for r in c.execute("SELECT id FROM blobs WHERE staged_for=? AND state='uploading'",(pid,))}
                blobs=references|uploads
                request=self._new_request(c,ctx,pid,'delete',len(references),len(uploads))
                c.execute('UPDATE profiles SET body=NULL,deleted=1,delete_request=? WHERE id=?',(request,pid))
                c.execute('DELETE FROM refs WHERE profile=?',(pid,))
                for blob in sorted(blobs):self._item(c,request,blob)
        self.checkpoint('logical_committed',request)
        return self.receipt(ctx,request)

    def receipt(self,ctx,request):
        self._context(ctx);identity(request)
        with self._tx() as c:
            row=c.execute('SELECT * FROM requests WHERE id=? AND tenant=? COLLATE BINARY',(request,ctx.tenant)).fetchone()
            if row is None:fail('request_missing')
            blocked=bool(c.execute('SELECT deleted FROM profiles WHERE id=?',(row['profile'],)).fetchone()[0])
            items=[dict(r) for r in c.execute('SELECT blob,status,reason,attempts,observed_ns FROM items WHERE request=? ORDER BY blob',(request,))]
            exact=row['cancelled_uploads'] is not None
            return dict(request=request,profile=row['profile'],tenant=row['tenant'],kind=row['kind'],logical_blocked=blocked,removed_references=row['removed'] if exact else None,cancelled_uploads=row['cancelled_uploads'],counts_exact=exact,legacy_unverified_candidates=None if exact else row['removed'],items=items,settled=all(i['status'] in ('SHARED','UNLINKED','ABSENT') for i in items),physical_erasure='NOT_VERIFIED',observed_scope='Owned paths at recorded observations; already-open readers/copies/backups not revoked')

    @staticmethod
    def _observe(c,blob,status,reason):
        c.execute("UPDATE items SET status=?,reason=?,attempts=attempts+1,observed_ns=? WHERE blob=? AND status IN('PENDING','UNKNOWN')",(status,reason,time.time_ns(),blob))
        if status in ('UNLINKED','ABSENT'):c.execute("UPDATE blobs SET state='gone',digest=NULL,staged_for=NULL WHERE id=?",(blob,))

    def reconcile(self,ctx,*,limit=100):
        self._context(ctx);integer(limit,1,256)
        if not ctx.operator:fail('operator_required')
        processed=[]
        with self._tx() as c:
            try:sequence=int(self._setting(c,'work_sequence'))
            except (TypeError,ValueError):fail('invalid_schema')
            sequence=max(sequence,c.execute('SELECT coalesce(max(work_seq),0) FROM blobs').fetchone()[0])
            integer(sequence,0,2**63-257)
            rows=c.execute("SELECT * FROM blobs WHERE state IN('deleting','uploading') ORDER BY work_seq,id LIMIT ?",(limit,)).fetchall()
            for row in rows:
                blob=row['id'];path=files.blob_path(self.root,blob)
                sequence+=1;c.execute('UPDATE blobs SET work_seq=? WHERE id=?',(sequence,blob))
                if row['state']=='uploading':
                    processed.append(self._recover_upload(c,row,path));continue
                if c.execute('SELECT 1 FROM refs WHERE blob=?',(blob,)).fetchone():
                    self._observe(c,blob,'UNKNOWN','reference_conflict');processed.append(dict(blob=blob,status='UNKNOWN'));continue
                try:
                    info=path.lstat()
                    if not row['identity']:raise WitnessError('identity_missing')
                    expected=json.loads(row['identity'])
                    if files.path_identity(path)!=expected:raise WitnessError('file_changed')
                    files.regular(path);path.unlink()
                    self.checkpoint('unlinked_before_receipt',blob)
                    self._observe(c,blob,'UNLINKED','unlink_observed');status='UNLINKED'
                except FileNotFoundError:self._observe(c,blob,'ABSENT','path_absent');status='ABSENT'
                except (OSError,WitnessError,ValueError) as error:
                    reason=error.code if isinstance(error,WitnessError) else 'io_unknown'
                    self._observe(c,blob,'UNKNOWN',reason);status='UNKNOWN'
                processed.append(dict(blob=blob,status=status))
            c.execute("UPDATE settings SET value=? WHERE key='work_sequence'",(str(sequence),))
            pending=c.execute("SELECT count(*) FROM blobs WHERE state IN('deleting','uploading')").fetchone()[0]
        return dict(processed=processed,pending_blobs=pending,work_sequence=sequence,physical_erasure='NOT_VERIFIED')

    def _recover_upload(self,c,row,path):
        blob=row['id'];pid=row['staged_for']
        active=c.execute('SELECT tenant,deleted FROM profiles WHERE id=?',(pid,)).fetchone() if pid else None
        try:
            info=files.regular(path)
            if not active or active['deleted'] or not row['digest']:return dict(blob=blob,status='UNKNOWN',reason='upload_owner_missing')
            if row['upload_mode']=='legacy':return dict(blob=blob,status='UNKNOWN',reason='legacy_upload_operation_unknown')
            if row['upload_mode']=='put' and c.execute('SELECT count(*) FROM refs WHERE profile=?',(pid,)).fetchone()[0]>=MAX_REFS:return dict(blob=blob,status='UNKNOWN',reason='references_quota')
            if row['upload_mode']=='replace':
                if not row['replace_of'] or not c.execute('SELECT 1 FROM refs WHERE profile=? AND blob=?',(pid,row['replace_of'])).fetchone():return dict(blob=blob,status='UNKNOWN',reason='replacement_reference_changed')
                if c.execute('SELECT count(*) FROM requests').fetchone()[0]>=self.limits.requests:return dict(blob=blob,status='UNKNOWN',reason='requests_quota')
            expected=files.path_identity(path)
            files.read_checked(path,expected,row['digest'],self.limits.blob_bytes)
            c.execute("UPDATE blobs SET identity=?,state='ready',staged_for=NULL WHERE id=?",(json.dumps(expected),blob))
            if row['upload_mode']=='replace':
                request=self._new_request(c,Context(active['tenant']),pid,'replace',1)
                c.execute('DELETE FROM refs WHERE profile=? AND blob=?',(pid,row['replace_of']))
                self._item(c,request,row['replace_of'])
            self._link(c,active['tenant'],pid,blob)
            return dict(blob=blob,status='RECOVERED_REPLACEMENT' if row['upload_mode']=='replace' else 'RECOVERED_UPLOAD')
        except FileNotFoundError:
            c.execute("UPDATE blobs SET state='gone',digest=NULL,staged_for=NULL WHERE id=?",(blob,))
            return dict(blob=blob,status='ABSENT_UPLOAD')
        except (OSError,WitnessError,ValueError):return dict(blob=blob,status='UNKNOWN',reason='upload_identity_unknown')

    def inventory(self,ctx):
        self._context(ctx)
        if not ctx.operator:fail('operator_required')
        with self._tx() as c:
            rows=[dict(r) for r in c.execute('SELECT id,size,state FROM blobs ORDER BY id')]
            known={files.MARKER,*('blob_'+r['id']+'.bin' for r in rows)}
            count=0;unknown=0
            for child in self.root.iterdir():
                count+=1
                if count>20001:fail('inventory_limit')
                if child.name not in known:unknown+=1
            return dict(profiles=c.execute('SELECT count(*) FROM profiles').fetchone()[0],active_profiles=c.execute('SELECT count(*) FROM profiles WHERE deleted=0').fetchone()[0],references=c.execute('SELECT count(*) FROM refs').fetchone()[0],requests=c.execute('SELECT count(*) FROM requests').fetchone()[0],tracked_bytes=sum(r['size'] for r in rows if r['state']!='gone'),blobs=rows,unknown_directory_entries=unknown,limits=asdict(self.limits),sqlite_pages=c.execute('PRAGMA page_count').fetchone()[0],sqlite_page_limit=c.execute('PRAGMA max_page_count').fetchone()[0],scope='Logical tracked bytes, not RSS, disk allocation or backup erasure')
