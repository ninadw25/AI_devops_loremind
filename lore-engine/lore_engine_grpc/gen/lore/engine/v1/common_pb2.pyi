from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Source(_message.Message):
    __slots__ = ("type", "description", "thread_id", "score", "sensitivity")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    SENSITIVITY_FIELD_NUMBER: _ClassVar[int]
    type: str
    description: str
    thread_id: str
    score: float
    sensitivity: str
    def __init__(self, type: _Optional[str] = ..., description: _Optional[str] = ..., thread_id: _Optional[str] = ..., score: _Optional[float] = ..., sensitivity: _Optional[str] = ...) -> None: ...

class SourceList(_message.Message):
    __slots__ = ("sources",)
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    sources: _containers.RepeatedCompositeFieldContainer[Source]
    def __init__(self, sources: _Optional[_Iterable[_Union[Source, _Mapping]]] = ...) -> None: ...

class Done(_message.Message):
    __slots__ = ("confidence",)
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    confidence: str
    def __init__(self, confidence: _Optional[str] = ...) -> None: ...

class StreamError(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...
