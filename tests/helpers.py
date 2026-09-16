import asyncio
from collections.abc import Callable


async def wait_for(predicate: Callable[[], object], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("условие не выполнилось вовремя")
        await asyncio.sleep(0.01)
