"""Finite application values and private error mapping; auth is caller-owned."""
from dataclasses import dataclass
import json,re,uuid

PROFILE_BYTES=8192
BLOB_BYTES=1048576
MAX_TRACKED_BYTES=67108864
MAX_ROWS=10000
MAX_REFS=256
APP_ID=0x44575431
VERSION=1

class WitnessError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)

def fail(code):raise WitnessError(code)

def integer(value,lo,hi):
    if type(value) is not int or not lo<=value<=hi:fail('invalid_integer')
    return value

def name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value):fail('invalid_tenant')
    return value

def identity(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{32}',value):fail('invalid_identity')
    return value

def new_id():return uuid.uuid4().hex

def encode(value):
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,UnicodeError,RecursionError):fail('invalid_json')

def decode(raw):
    if not isinstance(raw,(bytes,str)):fail('invalid_json')
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:fail('duplicate_key')
            result[key]=value
        return result
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:fail('invalid_json'))
    except (ValueError,UnicodeError,RecursionError):fail('invalid_json')

def profile(value):
    if not isinstance(value,dict):fail('invalid_profile')
    raw=encode(value)
    if len(raw)>PROFILE_BYTES:fail('profile_size')
    return raw

@dataclass(frozen=True)
class Context:
    """Trusted previously authenticated tenant context, NOT an auth verifier."""
    tenant:str
    operator:bool=False
    def __post_init__(self):
        name(self.tenant)
        if type(self.operator) is not bool:fail('invalid_context')

@dataclass(frozen=True)
class Limits:
    profiles:int=1000
    blobs:int=1000
    requests:int=1000
    tracked_bytes:int=16777216
    blob_bytes:int=BLOB_BYTES
    def __post_init__(self):
        for value in (self.profiles,self.blobs,self.requests):integer(value,0,MAX_ROWS)
        integer(self.tracked_bytes,0,MAX_TRACKED_BYTES);integer(self.blob_bytes,0,BLOB_BYTES)
