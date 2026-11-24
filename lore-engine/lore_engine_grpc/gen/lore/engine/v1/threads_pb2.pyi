from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import wrappers_pb2 as _wrappers_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Thread(_message.Message):
    __slots__ = ("thread_id", "title", "turn_count", "total_tokens", "status", "created_at", "updated_at")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    TURN_COUNT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_TOKENS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    title: str
    turn_count: int
    total_tokens: int
    status: str
    created_at: str
    updated_at: str
    def __init__(self, thread_id: _Optional[str] = ..., title: _Optional[str] = ..., turn_count: _Optional[int] = ..., total_tokens: _Optional[int] = ..., status: _Optional[str] = ..., created_at: _Optional[str] = ..., updated_at: _Optional[str] = ...) -> None: ...

class Turn(_message.Message):
    __slots__ = ("turn_id", "sequence", "question", "answer", "status", "latency_ms", "created_at")
    TURN_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    QUESTION_FIELD_NUMBER: _ClassVar[int]
    ANSWER_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LATENCY_MS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    turn_id: str
    sequence: int
    question: str
    answer: str
    status: str
    latency_ms: _wrappers_pb2.Int32Value
    created_at: str
    def __init__(self, turn_id: _Optional[str] = ..., sequence: _Optional[int] = ..., question: _Optional[str] = ..., answer: _Optional[str] = ..., status: _Optional[str] = ..., latency_ms: _Optional[_Union[_wrappers_pb2.Int32Value, _Mapping]] = ..., created_at: _Optional[str] = ...) -> None: ...

class ListThreadsRequest(_message.Message):
    __slots__ = ("limit", "before_id")
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    BEFORE_ID_FIELD_NUMBER: _ClassVar[int]
    limit: int
    before_id: str
    def __init__(self, limit: _Optional[int] = ..., before_id: _Optional[str] = ...) -> None: ...

class ListThreadsResponse(_message.Message):
    __slots__ = ("threads",)
    THREADS_FIELD_NUMBER: _ClassVar[int]
    threads: _containers.RepeatedCompositeFieldContainer[Thread]
    def __init__(self, threads: _Optional[_Iterable[_Union[Thread, _Mapping]]] = ...) -> None: ...

class ListTurnsRequest(_message.Message):
    __slots__ = ("thread_id", "limit")
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    limit: int
    def __init__(self, thread_id: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class ListTurnsResponse(_message.Message):
    __slots__ = ("turns",)
    TURNS_FIELD_NUMBER: _ClassVar[int]
    turns: _containers.RepeatedCompositeFieldContainer[Turn]
    def __init__(self, turns: _Optional[_Iterable[_Union[Turn, _Mapping]]] = ...) -> None: ...

class DeleteThreadRequest(_message.Message):
    __slots__ = ("thread_id",)
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    thread_id: str
    def __init__(self, thread_id: _Optional[str] = ...) -> None: ...
