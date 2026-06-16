import asyncio

import pytest


@pytest.mark.asyncio
async def test_retained_startup_tasks_are_cancelled_on_shutdown():
    import app as app_mod

    async def sleeper():
        await asyncio.sleep(60)

    pending = asyncio.create_task(sleeper())
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    app_mod.app.state._startup_tasks = [pending, done]

    await app_mod._cancel_retained_startup_tasks()

    assert pending.cancelled()
    assert app_mod.app.state._startup_tasks == []
