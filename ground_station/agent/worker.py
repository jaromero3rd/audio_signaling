"""Keep SDK startup and model latency off the camera/RC thread."""

from __future__ import annotations

import asyncio
import queue
import threading

from ground_station.agent.controller import DroidController
from ground_station.agent.driver import MissionDriver


class DroidWorker:
    def __init__(self, driver: MissionDriver, *, model=None, timeout=60.0):
        self.driver = driver
        self.model = model
        self.timeout = timeout
        self.ready = threading.Event()
        self.failed = threading.Event()
        self._stop = threading.Event()
        self._queue: queue.Queue[tuple[str, dict]] = queue.Queue(maxsize=8)
        self._thread = None
        self._loop = None
        self._task = None

    def start(self, instruction: str) -> None:
        self.submit(instruction, {})
        self._thread = threading.Thread(target=self._run, name="agridrone-droid", daemon=True)
        self._thread.start()

    def submit(self, instruction: str, event: dict) -> bool:
        if self._stop.is_set() or self.failed.is_set():
            return False
        try:
            self._queue.put_nowait((instruction, event))
            return True
        except queue.Full:
            self.driver.stop()
            self.failed.set()
            return False

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self.driver.stop()
            self.failed.set()
            print(f"Droid controller stopped ({type(error).__name__}); mission abort requested.")
        finally:
            self.ready.set()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        async with DroidController(
            self.driver, model=self.model, timeout=self.timeout
        ) as controller:
            self.ready.set()
            while not self._stop.is_set() and not self.failed.is_set():
                try:
                    instruction, event = self._queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue
                await controller.handle(instruction, event)
                print("Droid turn complete; camera/RC control remains local.")

    def close(self) -> None:
        self.driver.stop()
        self._stop.set()
        if self._loop is not None and self._task is not None:
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=12)
            if self._thread.is_alive():
                print("Droid shutdown is still pending; mission remains stopped.")
