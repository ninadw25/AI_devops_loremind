from lore.engine.v1 import common_pb2 as _common_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class QueryRequest(_message.Message):
    __slots__ = ("query", "thread_id", "k")
    QUERY_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    K_FIELD_NUMBER: _ClassVar[int]
    query: str
    thread_id: str
    k: int
    def __init__(self, query: _Optional[str] = ..., thread_id: _Optional[str] = ..., k: _Optional[int] = ...) -> None: ...

class QueryResponse(_message.Message):
    __slots__ = ("answer", "confidence", "sources", "stats", "thread_id")
    ANSWER_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    STATS_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    answer: str
    confidence: str
    sources: _containers.RepeatedCompositeFieldContainer[_common_pb2.Source]
    stats: _struct_pb2.Struct
    thread_id: str
    def __init__(self, answer: _Optional[str] = ..., confidence: _Optional[str] = ..., sources: _Optional[_Iterable[_Union[_common_pb2.Source, _Mapping]]] = ..., stats: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., thread_id: _Optional[str] = ...) -> None: ...

class QueryStreamEvent(_message.Message):
    __slots__ = ("thread_id", "sources", "token", "done", "error")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    DONE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    sources: _common_pb2.SourceList
    token: str
    done: _common_pb2.Done
    error: _common_pb2.StreamError
    def __init__(self, thread_id: _Optional[str] = ..., sources: _Optional[_Union[_common_pb2.SourceList, _Mapping]] = ..., token: _Optional[str] = ..., done: _Optional[_Union[_common_pb2.Done, _Mapping]] = ..., error: _Optional[_Union[_common_pb2.StreamError, _Mapping]] = ...) -> None: ...
