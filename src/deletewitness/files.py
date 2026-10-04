"""Only flat generated blobs in one owned directory; no arbitrary path API."""
import hashlib,json,os,stat
from pathlib import Path
from .model import APP_ID,VERSION,WitnessError,encode,fail,identity

MARKER='.deletewitness-owner.json'
SIDES=('', '-journal','-wal','-shm')

def alias(st):return stat.S_ISLNK(st.st_mode) or bool(getattr(st,'st_file_attributes',0)&1024)

def absolute(path):
    try:result=Path(path).absolute()
    except (TypeError,ValueError):fail('invalid_path')
    for component in (result,*result.parents):
        if component.exists() or component.is_symlink():
            if alias(component.lstat()):fail('path_alias')
    return result.resolve()

def regular(path):
    info=path.lstat()
    if alias(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:fail('file_alias')
    return info

def file_identity(info):
    return dict(dev=info.st_dev,ino=info.st_ino,size=info.st_size,mtime=info.st_mtime_ns,ctime=info.st_ctime_ns)

if os.name=='nt':
    import ctypes,msvcrt
    from ctypes import wintypes
    class BasicInfo(ctypes.Structure):
        _fields_=[('creation',ctypes.c_longlong),('access',ctypes.c_longlong),('write',ctypes.c_longlong),('change',ctypes.c_longlong),('attributes',wintypes.DWORD)]
    _kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    _get_basic=_kernel.GetFileInformationByHandleEx
    _get_basic.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    _get_basic.restype=wintypes.BOOL

def handle_identity(fd):
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or alias(info):fail('file_alias')
    value=file_identity(info)
    if os.name=='nt':
        basic=BasicInfo()
        if not _get_basic(msvcrt.get_osfhandle(fd),0,ctypes.byref(basic),ctypes.sizeof(basic)):raise ctypes.WinError(ctypes.get_last_error())
        # Native ChangeTime, not Python's historically changing Windows ctime alias.
        value['ctime']=basic.change*100
    return value

def path_identity(path):
    info=regular(path)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    try:
        value=handle_identity(fd)
        if value['dev']!=info.st_dev or value['ino']!=info.st_ino:fail('file_changed')
        return value
    finally:os.close(fd)

def digest(raw):return hashlib.sha256(raw).hexdigest()

def blob_path(root,blob):return root/('blob_'+identity(blob)+'.bin')

def owned_header(path):
    regular(path)
    with path.open('rb') as stream:raw=stream.read(100)
    if len(raw)!=100 or raw[:16]!=b'SQLite format 3\x00' or int.from_bytes(raw[68:72],'big')!=APP_ID or int.from_bytes(raw[60:64],'big')!=VERSION:fail('foreign_database')

def namespaces(database,root):
    if root==database or root in database.parents:fail('database_in_attachment_root')
    targets=[Path(str(database)+suffix) for suffix in SIDES]
    if any(p==root or p in root.parents for p in targets):fail('namespace_collision')
    for p in targets:
        if p.exists() or p.is_symlink():
            info=regular(p)
            suffix=str(p)[len(str(database)):]
            if suffix in ('-wal','-shm'):fail('foreign_sidecar')
            if suffix=='-journal':
                with p.open('rb') as stream:header=stream.read(28)
                # DELETE-mode native hot or currently unflushed journal only.
                if len(header)<28 or header[:8] not in (bytes.fromhex('d9d505f920a163d7'),b'\x00'*8):fail('foreign_sidecar')

def read_marker(root):
    p=root/MARKER;regular(p)
    with p.open('rb') as stream:raw=stream.read(4097)
    if len(raw)>4096:fail('foreign_root')
    try:
        value=json.loads(raw)
        if not isinstance(value,dict):fail('foreign_root')
        return value
    except (ValueError,UnicodeError):fail('foreign_root')

def create_file(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    except BaseException:
        raise
    return path_identity(path)

def read_checked(path,expected,expected_digest,cap):
    initial=path_identity(path)
    if initial!=expected or initial['size']>cap:fail('file_changed')
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0)
    fd=os.open(path,flags)
    with os.fdopen(fd,'rb') as stream:
        if handle_identity(stream.fileno())!=expected:fail('file_changed')
        raw=stream.read(cap+1)
        if handle_identity(stream.fileno())!=expected:fail('file_changed')
    if path_identity(path)!=expected:fail('file_changed')
    if len(raw)!=expected['size'] or digest(raw)!=expected_digest:fail('file_changed')
    return raw
