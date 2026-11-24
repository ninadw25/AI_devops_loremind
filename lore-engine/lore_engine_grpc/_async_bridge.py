"""
lore_engine_grpc._async_bridge — run a *sync* generator as an async iterator.

The engine's query pipeline is synchronous (QueryService.query_stream is a
``Generator[dict, None, None]``). A grpc.aio server-streaming handler is an
async generator. Naively iterating the sync generator inline would block the
event loop for every LLM token; this bridge runs the generator in a worker
thread and ferries items back to the loop through a bounded queue, so the loop
stays responsive and concurrent RPCs don't serialize.

Cancellation semantics: if the async consumer stops (client disconnect / RPC
cancel), a flag tells the producer to stop after its current step and we drain
the queue so its blocking enqueue returns. Python threads cannot be force-
killed, so a producer stuck inside a long blocking LLM call outlives the
cancellation — that is the same pre-existing event-loop debt as the HTTP path
and is explicitly out of scope for LOR-111 (async-ification is its own ticket).
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator, Callable, Generator, TypeVar

T = TypeVar("T")

_ERR = object()        # sentinel marking an exception surfaced from the worker
_DONE = object()       # sentinel marking normal producer completion


def _drain(queue: asyncio.Queue) -> None:
    """Drop everything currently queued so a blocking producer-side put unblocks."""
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


async def aiter_sync_generator(
    factory: Callable[[], Generator[T, None, None]],
    executor: ThreadPoolExecutor,
) -> AsyncIterator[T]:
    """Yield items from a sync generator built by ``factory`` without blocking
    the event loop. ``factory`` is invoked inside the worker thread."""
    loop = asyncio.get_running_loop()
    # Capture the caller's contextvars so log correlation (request_id/user_id)
    # is visible to logs emitted from inside the pipeline running in the thread.
    ctx = contextvars.copy_context()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    stop = threading.Event()

    def _produce() -> None:
        try:
            gen = ctx.run(factory)
            try:
                while not stop.is_set():
                    try:
                        item = ctx.run(next, gen)
                    except StopIteration:
                        break
                    # Block until the loop accepts the item; bail out if the
                    # consumer has gone away (future cancelled / loop closed).
                    fut = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
                    fut.result()
            finally:
                close = getattr(gen, "close", None)
                if close is not None:
                    try:
                        ctx.run(close)
                    except Exception:  # noqa: BLE001 — generator cleanup is best-effort
                        pass
        except BaseException as exc:  # noqa: BLE001 — surface to the async side
            try:
                fut = asyncio.run_coroutine_threadsafe(queue.put((_ERR, exc)), loop)
                fut.result()
            except Exception:  # noqa: BLE001 — loop closing; nothing more we can do
                pass
        finally:
            try:
                fut = asyncio.run_coroutine_threadsafe(queue.put(_DONE), loop)
                fut.result()
            except Exception:  # noqa: BLE001 — loop closing
                pass

    producer = executor.submit(_produce)
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            if isinstance(item, tuple) and item and item[0] is _ERR:
                raise item[1]  # type: ignore[misc]
            yield item  # type: ignore[misc]
    finally:
        # Consumer gone: tell the producer to wind down, unblock its enqueue,
        # then wait for it to finish without tying up the event loop.
        stop.set()
        _drain(queue)
        if not producer.done():
            await loop.run_in_executor(None, lambda: producer.result())
