"""Actual profile/attachment application, independently checked SQL and filesystem."""
import json,sqlite3,tempfile
from contextlib import closing
from pathlib import Path
from deletewitness import Context,Store,WitnessError

def main():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp);db=p/'profiles.sqlite';root=p/'attachments'
        s=Store.initialize(db,root);a=Context('tenant_a',True);b=Context('tenant_b')
        alice=s.create_profile(a,{'display_name':'实验 Alice','contact':'alice@lab.invalid'})
        bob=s.create_profile(b,{'display_name':'Bob'})
        raw=b'controlled sample attachment\x00UTF-8:'+ '论文'.encode()
        blob=s.put_attachment(a,alice,raw)
        s.grant_attachment(a,alice,blob,b,bob)
        first=s.delete_profile(a,alice)
        try:s.get_profile(a,alice);raise AssertionError('tombstone consumer accepted')
        except WitnessError as e:assert e.code=='profile_deleted'
        assert s.read_attachment(b,bob,blob)==raw
        assert first['items'][0]['status']=='SHARED'
        second=s.delete_profile(b,bob);s.reconcile(a)
        done=s.receipt(b,second['request'])
        assert done['items'][0]['status']=='UNLINKED' and done['settled']
        assert not (root/('blob_'+blob+'.bin')).exists()
        with closing(sqlite3.connect(db)) as c:
            truth=c.execute('SELECT id,body,deleted FROM profiles ORDER BY id').fetchall()
            assert len(truth)==2 and all(body is None and deleted==1 for _,body,deleted in truth)
            assert c.execute('SELECT count(*) FROM refs').fetchone()[0]==0
            for row in c.execute('SELECT * FROM requests'):assert 'alice@lab.invalid' not in repr(row)
            assert c.execute('SELECT digest FROM blobs WHERE id=?',(blob,)).fetchone()[0] is None
        print(json.dumps(dict(actual_sql_profile_rows=2,actual_refs=0,shared_other_tenant_preserved=True,actual_attachment_path_absent=True,complete_receipt=done,physical_erasure='NOT_VERIFIED')))

if __name__=='__main__':main()
