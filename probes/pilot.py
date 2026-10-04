"""Actual native same-fixture controls; consumer oracle does not use core checkers."""
import argparse,hashlib,json,os,signal,sqlite3,subprocess,sys,time
from contextlib import closing
from pathlib import Path
from deletewitness import Context,Store

def sha(raw):return hashlib.sha256(raw).hexdigest()
def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def native_sql_only(db,pid):
    with closing(sqlite3.connect(db)) as c:
        c.execute('BEGIN IMMEDIATE');c.execute('UPDATE profiles SET body=NULL,deleted=1 WHERE id=?',(pid,));c.execute('DELETE FROM refs WHERE profile=?',(pid,));c.commit()
def native_unlink_only(db,root,pid):
    with closing(sqlite3.connect(db)) as c:
        rows=c.execute('SELECT blob FROM refs WHERE profile=? AND NOT EXISTS(SELECT 1 FROM refs other JOIN profiles live ON live.id=other.profile WHERE other.blob=refs.blob AND other.profile!=? AND live.deleted=0)',(pid,pid)).fetchall()
    for (blob,) in rows:(root/('blob_'+blob+'.bin')).unlink()
    return len(rows)

CHILD='''import json,os,sqlite3,sys,time
from contextlib import closing
from pathlib import Path
from deletewitness import Context,Store
db,root,pid,mode,phase=sys.argv[1:]
def ready(label,identifier=None):
 print(json.dumps(dict(pid=os.getpid(),phase=label,identifier=identifier)),flush=True)
 while True:time.sleep(1)
def checkpoint(label,identifier):
 if label==phase:ready(label,identifier)
if mode=='durable':
 s=Store(db,root,checkpoint=checkpoint)
 if phase=='logical_committed':s.delete_profile(Context('a',True),pid)
 else:s.reconcile(Context('a',True),limit=1)
elif mode=='sql-only':
 with closing(sqlite3.connect(db)) as c:
  c.execute('BEGIN IMMEDIATE');c.execute('UPDATE profiles SET body=NULL,deleted=1 WHERE id=?',(pid,));c.execute('DELETE FROM refs WHERE profile=?',(pid,));c.commit()
 ready('native_sql_committed')
else:
 with closing(sqlite3.connect(db)) as c:
  rows=c.execute('SELECT blob FROM refs WHERE profile=? AND NOT EXISTS(SELECT 1 FROM refs other JOIN profiles live ON live.id=other.profile WHERE other.blob=refs.blob AND other.profile!=? AND live.deleted=0)',(pid,pid)).fetchall()
 for (blob,) in rows:(Path(root)/('blob_'+blob+'.bin')).unlink()
 ready('native_reference_aware_unlink_completed')
'''

def kill_case(folder,db,root,pid,mode,phase):
    command=[sys.executable,'-I','-c',CHILD,str(db),str(root),pid,mode,phase]
    child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        line=child.stdout.readline();(folder/'child-ready.stdout').write_bytes(line);ready=json.loads(line)
        os.kill(ready['pid'],signal.SIGTERM);child.wait(timeout=10);stdout,stderr=child.communicate()
        (folder/'child-tail.stdout').write_bytes(stdout);(folder/'child.stderr').write_bytes(stderr)
        assert child.returncode!=0
        return dict(actual_pid=ready['pid'],actual_phase=ready['phase'],exit_code=child.returncode,command=command)
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)
        child.stdout.close();child.stderr.close()

def case(out,shape,mode):
    folder=out/(shape+'-'+mode);folder.mkdir();start=time.perf_counter();db=folder/'app.sqlite';root=folder/'files'
    s=Store.initialize(db,root);a=Context('a',True);b=Context('b');target=s.create_profile(a,{'display':'LAB deleted用户','private':'synthetic-private-profile'});other=s.create_profile(b,{'display':'LAB unaffected用户'})
    inputs={};target_blobs=[];other_blobs=[]
    if shape!='no-attachments':
        for n,size in enumerate((16384,65536)):
            body=(bytes(range(256))*((size+255)//256))[:size];blob=s.put_attachment(a,target,body)
            inputs[blob]=body;target_blobs.append(blob);(folder/('input-'+str(n)+'.bin')).write_bytes(body)
        body=b'other complete attachment\x00'+bytes(range(256))*32;blob=s.put_attachment(b,other,body)
        inputs[blob]=body;other_blobs.append(blob);(folder/'input-other.bin').write_bytes(body)
        if shape=='shared':s.grant_attachment(a,target,target_blobs[0],b,other);other_blobs.append(target_blobs[0])
    dump(folder/'fixture.json',dict(target=target,other=other,inputs={k:dict(bytes=len(v),sha256=sha(v)) for k,v in inputs.items()},target_blobs=target_blobs,other_blobs=other_blobs,original_profile={'display':'LAB deleted用户','private':'synthetic-private-profile'}))
    setup=time.perf_counter()-start;initial_db=db.stat().st_size;begin=time.perf_counter();request=None;kill=None;recoveries=[];native_unlinks=0
    if shape=='kill-primary':
        kill=kill_case(folder,db,root,target,mode,'logical_committed')
    elif shape=='kill-unlink':
        assert mode=='durable';request=s.delete_profile(a,target)['request'];kill=kill_case(folder,db,root,target,mode,'unlinked_before_receipt')
    elif mode=='durable':request=s.delete_profile(a,target)['request']
    elif mode=='sql-only':native_sql_only(db,target)
    else:native_unlinks=native_unlink_only(db,root,target)
    if mode=='durable':
        # Product recovery BEFORE independent native oracle, including hot journal.
        s=Store(db,root)
        if request is None:request=s.delete_profile(a,target)['request']
        for _ in range(10):
            step=s.reconcile(a,limit=1);recoveries.append(step)
            if not step['pending_blobs']:break
        receipt=s.receipt(a,request);assert receipt['settled'];dump(folder/'complete-receipt.json',receipt)
    else:receipt=None
    operation=time.perf_counter()-begin
    with closing(sqlite3.connect(db)) as c:
        target_row=c.execute('SELECT body,deleted FROM profiles WHERE id=?',(target,)).fetchone()
        all_refs=[dict(tenant=r[0],profile=r[1],blob=r[2]) for r in c.execute('SELECT tenant,profile,blob FROM refs ORDER BY profile,blob')]
        other_row=c.execute('SELECT body,deleted FROM profiles WHERE id=?',(other,)).fetchone()
        native_bytes=c.execute('PRAGMA page_size').fetchone()[0]*c.execute('PRAGMA page_count').fetchone()[0]
        # Actual reader uses the native live-flag/ref check and then complete bytes.
        live_other_refs=[r[0] for r in c.execute('SELECT blob FROM refs JOIN profiles live ON live.id=refs.profile WHERE refs.profile=? AND live.deleted=0 ORDER BY blob',(other,))]
        readable=not bool(target_row[1]) and target_row[0] is not None
        if mode=='durable':
            for table in ('requests','items'):assert 'synthetic-private-profile' not in repr(c.execute('SELECT * FROM '+table).fetchall())
    assert not other_row[1] and json.loads(other_row[0])=={'display':'LAB unaffected用户'}
    consumed=[]
    for blob in live_other_refs:
        raw=(root/('blob_'+blob+'.bin')).read_bytes();assert raw==inputs[blob]
        (folder/('consumed-other-'+blob+'.bin')).write_bytes(raw);consumed.append(dict(blob=blob,bytes=len(raw),sha256=sha(raw)))
    assert set(live_other_refs)==set(other_blobs)
    residual=[blob for blob in target_blobs if blob not in other_blobs and (root/('blob_'+blob+'.bin')).exists()]
    missing_shared=[blob for blob in other_blobs if not (root/('blob_'+blob+'.bin')).exists()]
    if mode=='durable':assert not readable and not residual and not missing_shared
    if mode=='sql-only':assert not readable
    if mode=='unlink-only':assert readable
    result=dict(shape=shape,mode=mode,setup_seconds=setup,deletion_recovery_seconds=operation,initial_sqlite_bytes=initial_db,final_sqlite_btree_bytes=native_bytes,remaining_file_logical_bytes=sum(p.stat().st_size for p in root.glob('blob_*.bin')),input_attachment_bytes=sum(map(len,inputs.values())),actual_target_profile_readable=readable,target_exclusive_residual_paths=residual,unaffected_shared_missing=missing_shared,all_unaffected_complete_bytes_match=True,whole_consumed_other=consumed,reference_rows=all_refs,actual_kill=kill,complete_receipt=receipt,reconciliation_calls=len(recoveries),reconciliations=recoveries,native_unlinks_in_parent=native_unlinks,manual_work=dict(unreferenced_target_file_candidates=len(residual),active_target_SQL_body_to_block=int(readable)),scope='Correct single-resource controls have narrower consistency semantics: SQL-only clears/block profile+refs but leaves file work; reference-aware unlink-only protects other users but leaves profile live. Not mature equivalent full deletion products. Same actual setup/inputs/arrival; single observations, no production savings.')
    dump(folder/'result.json',result);return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();out=Path(a.output).resolve();out.mkdir()
    cases=[case(out,shape,mode) for shape in ('ordinary','shared','no-attachments','kill-primary') for mode in ('sql-only','unlink-only','durable')]
    cases.append(case(out,'kill-unlink','durable'))
    result=dict(result='PASS',cases=cases,scope='Builder actual ordinary-installed SQLite/filesystem application,13native same-fixture cases including4actual process kills. Native SQL/complete binary oracle independent of product checker; retained limited-control/no-attachment equality and added cost. No certification or independent score.',kill_primary_comparability='Actual kill after each control primary resource phase: SQL-only and durable logical commit; unlink-only reference-aware unlink phase. No false same cross-resource semantics claim.',kill_unlink_scope='Additional actual durable unlink-before-receipt kill, not claimed independently reproduced by single-resource baselines.')
    dump(out/'result.json',result)
    print(json.dumps(dict(result='PASS',cases=len(cases),actual_kills=sum(c['actual_kill'] is not None for c in cases),all_unaffected_full_outputs_match=True)))
if __name__=='__main__':main()
