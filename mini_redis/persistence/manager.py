"""스냅샷을 언제, 어떤 순서로 찍을지 정해요: 주기나 AOF 크기가 넘으면 찍고, 옛 AOF를 지워요.

락 안에서는 메모리 복사와 AOF 경로 바꿔 끼우기만 해요. 파일 쓰기, fsync, 지우기는 모두 락 밖이에요.
"""

import logging
import os
import pathlib
import threading
import time
from collections.abc import Callable

from mini_redis import config
from mini_redis.persistence.aof import AofWriter
from mini_redis.persistence.layout import aof_name
from mini_redis.persistence.snapshot import delete_old_aofs, write_snapshot
from mini_redis.store import Store

logger = logging.getLogger(__name__)


class Snapshotter:
    """주기(interval_s)나 현재 AOF 크기(max_aof_bytes)를 보고 스냅샷을 찍어요.

    aof_number는 aof가 지금 적고 있는 파일 번호예요. 스냅샷을 찍을 때마다 하나씩 올라가요.
    """

    def __init__(
        self,
        data_dir: str | os.PathLike[str],
        store: Store,
        aof: AofWriter,
        aof_number: int,
        *,
        interval_s: float = config.SNAPSHOT_INTERVAL_S,
        max_aof_bytes: int = config.SNAPSHOT_AOF_MAX_BYTES,
        check_interval_s: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._data_dir = pathlib.Path(data_dir)
        self._store = store
        self._aof = aof
        self.aof_number = aof_number
        self._interval_s = interval_s
        self._max_aof_bytes = max_aof_bytes
        self._check_interval_s = check_interval_s
        self._monotonic = monotonic
        self._last = monotonic()  # 마지막 스냅샷 시각이에요. 처음에는 만든 시각이에요.
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot_lock = threading.Lock()  # 스냅샷끼리 겹쳐 돌지 않게 해요.

    def snapshot(self) -> bool:
        """스냅샷을 한 번 찍어요. 끝까지 갔으면 True, 중간에 실패했으면 False예요."""
        with self._snapshot_lock:
            return self._snapshot()

    def _snapshot(self) -> bool:
        next_number = self.aof_number + 1
        new_path = self._data_dir / aof_name(next_number)
        # 1. 락 안에서 복사와 AOF 전환을 한꺼번에 해요. 전환은 버퍼만 만져서 디스크 I/O가 없어요.
        try:
            entries = self._store.copy_for_snapshot(during_lock=lambda: self._aof.rotate(new_path))
        except Exception:
            logger.exception("스냅샷 복사 또는 AOF 전환에 실패했어요")
            return False
        self.aof_number = next_number
        self._last = self._monotonic()
        # 2. 옛 파일에 남은 마지막 줄을 밀어내요. 실패해도 줄은 pending에 남고, 아래 drop 전에는 지워지지 않아요.
        try:
            self._aof.flush()
        except Exception:
            logger.exception("옛 AOF 파일에 마지막 줄을 적지 못했어요")
        # 3. 스냅샷을 쓰고 교체해요. 실패하면 옛 스냅샷과 AOF 파일이 그대로라 복구는 여전히 맞아요.
        try:
            write_snapshot(self._data_dir, entries, [], next_aof=next_number)
        except Exception:
            logger.exception("스냅샷 파일 쓰기에 실패했어요")
            return False
        # 4. 교체가 끝났으니 pending 줄의 결과는 스냅샷 안에 있어요. 버리고 옛 AOF를 지워요.
        # 이미 진행 중이던 flush가 지워진 옛 파일에 한 번 더 적을 수 있어요. 복구는 스냅샷의 next_aof보다
        # 작은 번호의 파일을 무시하고, 그 파일은 다음 스냅샷이 지워요.
        self._aof.drop_pending()
        try:
            delete_old_aofs(self._data_dir, before=next_number)
        except Exception:
            logger.exception("옛 AOF 파일을 지우지 못했어요")
        return True

    def maybe_snapshot(self) -> bool:
        """주기가 지났거나 현재 AOF가 너무 크면 스냅샷을 찍어요. 찍었는지 돌려줘요."""
        due = self._monotonic() - self._last >= self._interval_s
        if due or self._aof.current_bytes > self._max_aof_bytes:
            return self.snapshot()
        return False

    def start(self) -> None:
        """check_interval_s마다 maybe_snapshot을 부르는 데몬 스레드를 켜요. 이미 켜져 있으면 그대로 둬요."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """스레드를 멈추고 기다려요. start 없이, 또는 두 번 불러도 돼요."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._check_interval_s):
            try:
                self.maybe_snapshot()
            except Exception:
                logger.exception("스냅샷 확인 중 예외가 났어요")
