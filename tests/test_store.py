import json,os,signal,sqlite3,subprocess,sys,tempfile,threading,unittest
from contextlib import closing
from pathlib import Path
from deletewitness import Context,Limits,Store,WitnessError

class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.db=self.base/'app.sqlite';self.root=self.base/'attachments'
        self.ctx=Context('tenant',True);self.other=Context('other')
    def tearDown(self):self.tmp.cleanup()
    def make(self,limits=Limits()):return Store.initialize(self.db,self.root,limits)
    def refusal(self,code,fn):
        with self.assertRaises(WitnessError) as r:fn()
        self.assertEqual(r.exception.code,code)
    def path(self,blob):return self.root/('blob_'+blob+'.bin')

    def test_complete_shared_native_truth_and_private_ledger(self):
        s=self.make();a=s.create_profile(self.ctx,{'secret':'original-personal-content'});b=s.create_profile(self.other,{'name':'B'})
        raw=b'actual binary\x00'+ '文件'.encode();blob=s.put_attachment(self.ctx,a,raw)
        s.grant_attachment(self.ctx,a,blob,self.other,b)
        first=s.delete_profile(self.ctx,a)
        self.assertEqual(first['items'][0]['status'],'SHARED');self.assertEqual(s.read_attachment(self.other,b,blob),raw)
        self.refusal('profile_deleted',lambda:s.get_profile(self.ctx,a))
        self.assertEqual(s.delete_profile(self.ctx,a)['request'],first['request'])
        second=s.delete_profile(self.other,b);s.reconcile(self.ctx)
        done=s.receipt(self.other,second['request']);self.assertTrue(done['settled']);self.assertFalse(self.path(blob).exists())
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM refs').fetchone()[0],0)
            self.assertTrue(all(body is None and deleted==1 for body,deleted in c.execute('SELECT body,deleted FROM profiles')))
            self.assertIsNone(c.execute('SELECT digest FROM blobs').fetchone()[0])
            for table in ('requests','items'):
                self.assertNotIn('original-personal-content',repr(c.execute('SELECT * FROM '+table).fetchall()))

    def test_tenant_refusal_and_no_unauthorized_link(self):
        s=self.make();a=s.create_profile(self.ctx,{});b=s.create_profile(self.other,{})
        blob=s.put_attachment(self.ctx,a,b'private')
        self.refusal('profile_missing',lambda:s.get_profile(self.other,a))
        self.refusal('attachment_missing',lambda:s.link_attachment(self.other,b,blob))
        self.refusal('operator_required',lambda:s.grant_attachment(Context('tenant'),a,blob,self.other,b))
        self.refusal('request_missing',lambda:s.receipt(self.other,s.delete_profile(self.ctx,a)['request']))

    def test_budget_exact_minus_one_plus_one_and_zero(self):
        s=self.make(Limits(profiles=1,blobs=2,requests=1,tracked_bytes=4,blob_bytes=4));p=s.create_profile(self.ctx,{})
        self.refusal('profiles_quota',lambda:s.create_profile(self.ctx,{}))
        self.refusal('attachment_size',lambda:s.put_attachment(self.ctx,p,b'12345'))
        blob=s.put_attachment(self.ctx,p,b'1234')
        self.refusal('tracked_bytes_quota',lambda:s.put_attachment(self.ctx,p,b'x'))
        request=s.delete_profile(self.ctx,p);s.reconcile(self.ctx)
        self.assertTrue(s.receipt(self.ctx,request['request'])['settled'])
        self.assertEqual(s.inventory(self.ctx)['tracked_bytes'],0)
        for value in (-1,True,10001):self.refusal('invalid_integer',lambda:Limits(profiles=value))
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);z=Store.initialize(r/'zero.db',r/'files',Limits(tracked_bytes=0,blob_bytes=0));p=z.create_profile(self.ctx,{})
            b=z.put_attachment(self.ctx,p,b'');self.assertEqual(z.read_attachment(self.ctx,p,b),b'')
            self.refusal('attachment_size',lambda:z.put_attachment(self.ctx,p,b'x'))
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);z=Store.initialize(r/'minus.db',r/'files',Limits(tracked_bytes=3));p=z.create_profile(self.ctx,{})
            self.refusal('tracked_bytes_quota',lambda:z.put_attachment(self.ctx,p,b'1234'))

    def test_deletion_quota_refuses_before_logical_change(self):
        s=self.make(Limits(requests=0));p=s.create_profile(self.ctx,{'still':'live'})
        self.refusal('requests_quota',lambda:s.delete_profile(self.ctx,p))
        self.assertEqual(s.get_profile(self.ctx,p)['profile'],{'still':'live'})

    def test_replacement_quota_refuses_before_upload_and_file_effect(self):
        s=self.make(Limits(requests=0));p=s.create_profile(self.ctx,{});old=s.put_attachment(self.ctx,p,b'old')
        before=s.inventory(self.ctx)
        self.refusal('requests_quota',lambda:s.put_attachment(self.ctx,p,b'new',replace=old))
        self.assertEqual(s.inventory(self.ctx),before);s.reconcile(self.ctx)
        self.assertEqual(s.get_profile(self.ctx,p)['attachments'],[old]);self.assertEqual(s.read_attachment(self.ctx,p,old),b'old')

    def test_owned_legacy_active_data_migrates_without_guessing_upload(self):
        s=self.make();p=s.create_profile(self.ctx,{'still':'live'});blob=s.put_attachment(self.ctx,p,b'legacy data')
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('ALTER TABLE blobs DROP COLUMN replace_of');c.execute('ALTER TABLE blobs DROP COLUMN upload_mode');c.commit()
        reopened=Store(self.db,self.root)
        self.assertEqual(reopened.get_profile(self.ctx,p)['attachments'],[blob])
        self.assertEqual(reopened.read_attachment(self.ctx,p,blob),b'legacy data')

    def test_detected_overwrite_is_unknown_and_preserved(self):
        s=self.make();p=s.create_profile(self.ctx,{});blob=s.put_attachment(self.ctx,p,b'original')
        request=s.delete_profile(self.ctx,p);self.path(blob).write_bytes(b'NEW unrelated replacement')
        s.reconcile(self.ctx);done=s.receipt(self.ctx,request['request'])
        self.assertEqual(done['items'][0]['status'],'UNKNOWN');self.assertFalse(done['settled'])
        self.assertEqual(self.path(blob).read_bytes(),b'NEW unrelated replacement')

    def test_hardlink_detection_preserves_other_path(self):
        s=self.make();p=s.create_profile(self.ctx,{});blob=s.put_attachment(self.ctx,p,b'linked')
        outside=self.base/'outside.bin';os.link(self.path(blob),outside)
        request=s.delete_profile(self.ctx,p);s.reconcile(self.ctx)
        self.assertEqual(s.receipt(self.ctx,request['request'])['items'][0]['status'],'UNKNOWN')
        self.assertEqual(outside.read_bytes(),b'linked');self.assertTrue(self.path(blob).exists())

    def test_replace_atomic_reference_and_old_reconciliation(self):
        s=self.make();p=s.create_profile(self.ctx,{});old=s.put_attachment(self.ctx,p,b'old')
        new=s.put_attachment(self.ctx,p,b'new-version',replace=old)
        self.assertEqual(s.get_profile(self.ctx,p)['attachments'],[new]);self.assertEqual(s.read_attachment(self.ctx,p,new),b'new-version')
        self.refusal('reference_missing',lambda:s.read_attachment(self.ctx,p,old))
        s.reconcile(self.ctx);self.assertFalse(self.path(old).exists());self.assertTrue(self.path(new).exists())

    def test_allocated_upload_deleted_before_create_cannot_reappear(self):
        s=self.make();p=s.create_profile(self.ctx,{});ready=threading.Event();proceed=threading.Event();errors=[]
        def checkpoint(phase,_):
            if phase=='upload_allocated':ready.set();self.assertTrue(proceed.wait(5))
        uploader=Store(self.db,self.root,checkpoint=checkpoint)
        def put():
            try:uploader.put_attachment(self.ctx,p,b'pending')
            except WitnessError as e:errors.append(e.code)
        t=threading.Thread(target=put);t.start();self.assertTrue(ready.wait(5))
        request=s.delete_profile(self.ctx,p);proceed.set();t.join(5)
        self.assertFalse(t.is_alive());self.assertEqual(errors,['profile_deleted'])
        self.assertEqual(request['removed_references'],0);self.assertEqual(request['cancelled_uploads'],1);self.assertTrue(request['counts_exact'])
        s.reconcile(self.ctx);self.assertTrue(s.receipt(self.ctx,request['request'])['settled'])
        self.assertFalse(list(self.root.glob('blob_*.bin')))

    def test_old_receipt_count_uncertainty_is_preserved(self):
        s=self.make();p=s.create_profile(self.ctx,{});s.put_attachment(self.ctx,p,b'old')
        request=s.delete_profile(self.ctx,p)['request']
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('ALTER TABLE requests DROP COLUMN cancelled_uploads');c.commit()
        recovered=Store(self.db,self.root);view=recovered.receipt(self.ctx,request)
        self.assertFalse(view['counts_exact']);self.assertIsNone(view['removed_references']);self.assertEqual(view['legacy_unverified_candidates'],1)
        recovered.reconcile(self.ctx);self.assertTrue(recovered.receipt(self.ctx,request)['settled'])

    def kill_at(self,phase):
        s=self.make();p=s.create_profile(self.ctx,{'private':'kill-fixture'});blob=s.put_attachment(self.ctx,p,b'kill actual file')
        if phase=='unlinked_before_receipt':request=s.delete_profile(self.ctx,p)['request']
        else:request=None
        code='''import json,os,sys,time
from deletewitness import Store,Context
def checkpoint(phase,identifier):
 if phase==sys.argv[4]:
  print(json.dumps(dict(phase=phase,identifier=identifier,pid=os.getpid())),flush=True)
  while True:time.sleep(1)
s=Store(sys.argv[1],sys.argv[2],checkpoint=checkpoint)
if sys.argv[4]=='logical_committed':s.delete_profile(Context('tenant',True),sys.argv[3])
else:s.reconcile(Context('tenant',True))
'''
        process=subprocess.Popen([sys.executable,'-I','-c',code,str(self.db),str(self.root),p,phase],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            ready=json.loads(process.stdout.readline());self.assertEqual(ready['phase'],phase)
            os.kill(ready['pid'],signal.SIGTERM);process.wait(timeout=10)
            self.assertNotEqual(process.returncode,0)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
            process.stdout.close();process.stderr.close()
        # Public product must recover first, BEFORE any raw SQLite oracle opens.
        recovered=Store(self.db,self.root)
        if request is None:request=recovered.delete_profile(self.ctx,p)['request']
        recovered.reconcile(self.ctx);done=recovered.receipt(self.ctx,request)
        self.assertTrue(done['settled']);self.assertFalse(self.path(blob).exists())
        self.assertEqual(done['items'][0]['status'],'UNLINKED' if phase=='logical_committed' else 'ABSENT')
        with closing(sqlite3.connect(self.db)) as c:self.assertEqual(c.execute('SELECT body,deleted FROM profiles WHERE id=?',(p,)).fetchone(),(None,1))

    def test_actual_parent_kill_after_logical_commit(self):self.kill_at('logical_committed')
    def test_actual_parent_kill_after_unlink_before_receipt(self):self.kill_at('unlinked_before_receipt')

    def test_foreign_root_database_namespace_and_sidecar_preserved(self):
        foreign=self.base/'foreign.db'
        with closing(sqlite3.connect(foreign)) as c:
            c.execute('CREATE TABLE x(value)');c.execute('INSERT INTO x VALUES(17)');c.commit()
        before=foreign.read_bytes();self.root.mkdir();(self.root/'foreign.bin').write_bytes(b'untouched')
        self.refusal('database_exists',lambda:Store.initialize(foreign,self.root))
        self.assertEqual(foreign.read_bytes(),before);self.assertEqual((self.root/'foreign.bin').read_bytes(),b'untouched')
        self.refusal('database_in_attachment_root',lambda:Store.initialize(self.root/'app.db',self.root))
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);s=Store.initialize(r/'app.db',r/'files');journal=Path(str(s.database)+'-journal');journal.write_bytes(b'foreign journal ordinary bytes')
            before=journal.read_bytes();self.refusal('foreign_sidecar',lambda:Store(s.database,s.root));self.assertEqual(journal.read_bytes(),before)

    def test_registered_cli_complete_binary_read_and_refused_consumer(self):
        cli=Path(sys.executable).parent/('deletewitness.exe' if os.name=='nt' else 'deletewitness')
        common=[str(cli),'--database',str(self.db),'--root',str(self.root),'--tenant','tenant']
        def run(args,expected=0):
            r=subprocess.run(common+args,capture_output=True,timeout=10);self.assertEqual(r.returncode,expected,r.stderr.decode());return r.stdout
        run(['init']);source=self.base/'profile.json';source.write_text('{"name":"CLI 用户"}',encoding='utf-8')
        p=json.loads(run(['create','--input',str(source)]))['profile'];attachment=self.base/'input.bin';raw=bytes(range(256))*3;attachment.write_bytes(raw)
        blob=json.loads(run(['put','--profile',p,'--input',str(attachment)]))['blob']
        self.assertEqual(run(['read','--profile',p,'--blob',blob]),raw)
        request=json.loads(run(['delete','--profile',p]))['request']
        self.assertEqual(json.loads(run(['get','--profile',p],3))['error'],'profile_deleted')
        run(['--operator','reconcile']);done=json.loads(run(['receipt','--request',request]));self.assertTrue(done['settled'])
        self.assertFalse(self.path(blob).exists())

if __name__=='__main__':unittest.main()
