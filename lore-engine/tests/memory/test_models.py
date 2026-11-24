from datetime import datetime
from memory.models.message import Message
from memory.models.turn import Turn
from memory.models.thread import Thread
from memory.ids import new_ulid


def test_message_roles():
    m = Message(role="user", content="hello")
    assert m.role == "user"
    assert m.content == "hello"
    assert m.metadata == {}


def test_turn_defaults():
    tid = new_ulid()
    thid = new_ulid()
    t = Turn(
        turn_id=tid,
        thread_id=thid,
        user_id="u1",
        sequence=0,
        user_message=Message(role="user", content="q"),
    )
    assert t.status == "pending"
    assert t.assistant_message is None
    assert t.retrieved_context == []
    assert isinstance(t.created_at, datetime)


def test_thread_defaults():
    th = Thread(thread_id=new_ulid(), user_id="u1", title="first question")
    assert th.status == "active"
    assert th.turn_count == 0
    assert th.summary is None
