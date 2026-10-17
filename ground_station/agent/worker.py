"""Keep SDK startup and model latency off the camera/RC thread."""

from __future__ import annotations

import asyncio
import queue
import threading
from collections import deque
from pathlib import Path

from ground_station.agent.controller import DroidController
from ground_station.agent.driver import MissionDriver


class DroidWorker:
    def __init__(self, driver: MissionDriver, *, model=None, timeout=60.0,
                 machine_id=None, pipeline=None, controller_factory=DroidController,
                 on_result=None):
        self.driver = driver
        self.model = model
        self.timeout = timeout
        self.machine_id = machine_id
        self.pipeline = pipeline
        self.controller_factory = controller_factory
        self.on_result = on_result
        self.results = deque(maxlen=8)
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

    def submit_photo(self, path: Path | None) -> bool:
        if path is None or not path.is_file() or self.pipeline is None:
            self.driver.stop()
            self.failed.set()
            return False
        try:
            capture_id = self.driver.begin_analysis()
        except RuntimeError:
            return False
        return self.submit("", {"kind": "_host_photo", "path": path, "capture_id": capture_id})

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
        try:
            async with self.controller_factory(
                self.driver,
                model=self.model,
                timeout=self.timeout,
                machine_id=self.machine_id,
                pipeline=self.pipeline,
            ) as controller:
                self.ready.set()
                while not self._stop.is_set() and not self.failed.is_set():
                    try:
                        instruction, event = self._queue.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.05)
                        continue
                    if event.get("kind") == "_host_photo":
                        result = await self.pipeline.process_photo(controller, event["path"], event["capture_id"])
                    else:
                        result = await controller.handle(instruction, event)
                    self.results.append(result)
                    print(f"Droid: {result.get('message', result.get('reason', 'turn complete'))}")
                    if self.on_result is not None:
                        self.on_result(result)
        finally:
            if self.pipeline is not None:
                await self.pipeline.close()

    def close(self) -> None:
        self.driver.stop()
        self._stop.set()
        if self._thread is None and self.pipeline is not None:
            asyncio.run(self.pipeline.close())
        if self._loop is not None and self._task is not None:
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=12)
            if self._thread.is_alive():
                print("Droid shutdown is still pending; mission remains stopped.")


class DroidVoiceGateway:
    """Voice submits intent to Droid; emergency stop bypasses cloud latency."""

    controlled_by_droid = True

    def __init__(self, worker, driver):
        self.worker = worker
        self.driver = driver

    def request_start(self):
        queued = self.worker.submit("The operator requested a survey. Read status and request start_survey.", {"kind": "voice_start"})
        return {"ok": queued, "status": "queued_for_droid" if queued else "rejected", "executed": False}

    def request_abort(self):
        return self.driver.stop()

    def snapshot(self):
        return self.driver.snapshot()
