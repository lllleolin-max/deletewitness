"""Registered application CLI; content reads are explicit, diagnostics are private."""
import argparse,json,sys
from pathlib import Path
from .model import Context,Limits,WitnessError,decode,encode,fail
from .store import Store

def bounded_file(path,cap):
    try:
        with Path(path).open('rb') as stream:raw=stream.read(cap+1)
    except OSError:fail('input_unknown')
    if len(raw)>cap:fail('input_size')
    return raw

def main(argv=None):
    p=argparse.ArgumentParser(prog='deletewitness')
    p.add_argument('--database',required=True);p.add_argument('--root',required=True)
    p.add_argument('--tenant',default='demo');p.add_argument('--operator',action='store_true')
    sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init')
    for key,default in [('profiles',1000),('blobs',1000),('requests',1000),('tracked-bytes',16777216),('blob-bytes',1048576)]:init.add_argument('--'+key,type=int,default=default)
    create=sub.add_parser('create');create.add_argument('--input',required=True)
    for command in ('get','delete'):
        q=sub.add_parser(command);q.add_argument('--profile',required=True)
    put=sub.add_parser('put');put.add_argument('--profile',required=True);put.add_argument('--input',required=True);put.add_argument('--replace')
    for command in ('read','link'):
        q=sub.add_parser(command);q.add_argument('--profile',required=True);q.add_argument('--blob',required=True)
    grant=sub.add_parser('grant');grant.add_argument('--profile',required=True);grant.add_argument('--blob',required=True);grant.add_argument('--target-tenant',required=True);grant.add_argument('--target-profile',required=True)
    receipt=sub.add_parser('receipt');receipt.add_argument('--request',required=True)
    reconcile=sub.add_parser('reconcile');reconcile.add_argument('--limit',type=int,default=100)
    sub.add_parser('inventory');a=p.parse_args(argv)
    try:
        ctx=Context(a.tenant,a.operator)
        if a.command=='init':
            limits=Limits(a.profiles,a.blobs,a.requests,a.tracked_bytes,a.blob_bytes)
            Store.initialize(a.database,a.root,limits);result=dict(initialized=True,limits=limits.__dict__)
        else:
            store=Store(a.database,a.root)
            if a.command=='create':result=dict(profile=store.create_profile(ctx,decode(bounded_file(a.input,65536))))
            elif a.command=='get':result=store.get_profile(ctx,a.profile)
            elif a.command=='put':result=dict(blob=store.put_attachment(ctx,a.profile,bounded_file(a.input,store.limits.blob_bytes),replace=a.replace))
            elif a.command=='read':sys.stdout.buffer.write(store.read_attachment(ctx,a.profile,a.blob));return 0
            elif a.command=='link':store.link_attachment(ctx,a.profile,a.blob);result=dict(linked=True)
            elif a.command=='grant':store.grant_attachment(ctx,a.profile,a.blob,Context(a.target_tenant),a.target_profile);result=dict(granted=True)
            elif a.command=='delete':result=store.delete_profile(ctx,a.profile)
            elif a.command=='receipt':result=store.receipt(ctx,a.request)
            elif a.command=='reconcile':result=store.reconcile(ctx,limit=a.limit)
            else:result=store.inventory(ctx)
        sys.stdout.buffer.write(encode(result)+b'\n');return 0
    except WitnessError as error:
        sys.stdout.buffer.write(encode(dict(error=error.code,outcome='REFUSED_OR_UNKNOWN',scope='See operation contract; an error does not roll back a prior committed DB/FS effect'))+b'\n');return 3
    except (OSError,ValueError):
        sys.stdout.buffer.write(b'{"error":"operation_unknown","outcome":"UNKNOWN"}\n');return 3

if __name__=='__main__':raise SystemExit(main())
