"""AdaptiveLimiter (claim extraction throughput): the AIMD core.

Pins the control-loop properties that make it safe to run hot:

  1. a 429 halves the in-flight limit immediately (multiplicative decrease)
     and never below the floor;
  2. a clean streak raises the limit by one (additive increase) and never
     above the cap;
  3. acquire blocks at the limit and unblocks when a slot frees or the
     limit rises — no lost wakeups, no overshoot.
"""
import asyncio

from runners.extract_claims import AdaptiveLimiter, _is_throttle


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_penalize_halves_and_floors():
    async def go():
        lim = AdaptiveLimiter(start=16, floor=2)
        await lim.penalize()
        assert lim.limit == 8
        for _ in range(10):
            await lim.penalize()
        assert lim.limit == 2, "never drops below the floor"
    run(go())


def test_reward_streak_climbs_to_cap_only():
    async def go():
        lim = AdaptiveLimiter(start=4, cap=5, streak=3)
        for _ in range(3):
            await lim.reward()
        assert lim.limit == 5, "a full clean streak adds one"
        for _ in range(30):
            await lim.reward()
        assert lim.limit == 5, "never climbs past the cap"
    run(go())


def test_acquire_blocks_at_limit_and_unblocks_on_release():
    async def go():
        lim = AdaptiveLimiter(start=2, floor=1)
        await lim.acquire()
        await lim.acquire()
        third = asyncio.ensure_future(lim.acquire())
        await asyncio.sleep(0.01)
        assert not third.done(), "third acquire must wait at limit=2"
        await lim.release()
        await asyncio.wait_for(third, timeout=1)
    run(go())


def test_throttle_detection():
    assert _is_throttle(Exception("Error code: 429 - ThrottlingException"))
    assert _is_throttle(Exception("TooManyRequestsException"))
    assert not _is_throttle(Exception("connection reset by peer"))
