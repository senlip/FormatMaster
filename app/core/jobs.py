"""任务模型与调度器。

调度器是纯 Python 线程实现，不依赖 Qt，方便单独测试；
UI 层通过回调把事件转成 Qt 信号（跨线程 emit 是安全的）。

关于国内平台加密格式（.ncm/.qmc*/.kgm/.kwm ...）：
调度时先调 core.decryptors 剥壳，再走正常引擎流程，对上层完全透明。
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from .decryptors import decryptor_for_ext
from .engines.base import ConvertOptions, ConvertResult, MediaInfo
from .formats import ORIGINAL_EXT, Kind, kind_of, label_of
from .registry import EngineRegistry, get_registry


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"

    @property
    def label(self) -> str:
        return {
            JobStatus.PENDING: "排队中",
            JobStatus.RUNNING: "转换中",
            JobStatus.DONE: "已完成",
            JobStatus.FAILED: "失败",
            JobStatus.CANCELLED: "已取消",
            JobStatus.SKIPPED: "已跳过",
        }[self]


def unique_path(path: Path) -> Path:
    """目标已存在且不允许覆盖时，自动加 (1)(2) 后缀。"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(1, 1000):
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
    return parent / f"{stem} ({int(time.time())}){suffix}"


@dataclass
class Job:
    """一个转换任务。"""

    src: Path
    options: ConvertOptions
    out_dir: Path
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    message: str = "等待中"
    elapsed: float = 0.0
    output: Path | None = None
    info: MediaInfo | None = None
    engine_name: str = ""
    #: 源文件来自哪个平台（仅加密格式有值）
    platform: str = ""
    #: 剥壳后的真实格式（仅加密格式有值）
    real_ext: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    # ------------------------------------------------------------------ #
    @property
    def src_ext(self) -> str:
        return self.src.suffix.lstrip(".").lower()

    @property
    def dst_ext(self) -> str:
        return self.options.target_ext.lower().lstrip(".")

    @property
    def src_kind(self) -> Kind | None:
        return kind_of(self.src_ext)

    @property
    def target_path(self) -> Path:
        return self.out_dir / f"{self.src.stem}.{self.dst_ext}"

    def target_for(self, ext: str) -> Path:
        """按指定扩展名算输出路径（加密格式剥壳后真实格式会变，故作此方法）。"""
        ext = ext.lower().lstrip(".")
        return self.out_dir / f"{self.src.stem}.{ext}"

    @property
    def is_finished(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.SKIPPED)

    @property
    def summary(self) -> str:
        if self.status is JobStatus.DONE and self.output:
            return f"{self.output.name}  ·  {self.elapsed:.1f}s"
        return self.message

    def reset(self) -> None:
        self.status = JobStatus.PENDING
        self.progress = 0.0
        self.message = "等待中"
        self.elapsed = 0.0
        self.output = None
        self.platform = ""
        self.real_ext = ""
        self.cancel_event = threading.Event()


# --------------------------------------------------------------------------- #
class Scheduler:
    """批量任务调度器。

    设计取舍：FFmpeg 自身已是多线程，盲目开大并发只会让磁盘成为瓶颈，
    所以默认并发数 2，可在设置里调整。
    """

    def __init__(self, registry: EngineRegistry | None = None, max_workers: int = 2) -> None:
        self.registry = registry or get_registry()
        self._max_workers = max(1, int(max_workers))
        self._pool: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._futures: dict[str, object] = {}
        self._running = False

        # 回调（由 UI 赋值）
        self.on_job_start: Callable[[Job], None] | None = None
        self.on_job_progress: Callable[[Job], None] | None = None
        self.on_job_finish: Callable[[Job], None] | None = None
        self.on_all_finished: Callable[[], None] | None = None

    # ------------------------------------------------------------------ #
    @property
    def max_workers(self) -> int:
        return self._max_workers

    @max_workers.setter
    def max_workers(self, value: int) -> None:
        self._max_workers = max(1, int(value))

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ #
    def run(self, jobs: list[Job]) -> int:
        """提交一批任务，返回真正被提交的数量。"""
        pending = [j for j in jobs if not j.is_finished]
        if not pending:
            return 0

        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=self._max_workers, thread_name_prefix="fm-worker"
                )
            pool = self._pool
            self._running = True

        # 分三步：提交 → 登记 → 挂回调。
        #
        # 关键约束：绝不能持锁挂回调。任务有可能在极短时间内结束（例如加密格式
        # 探测阶段直接报错，微秒级返回），此时 Future 已完成，add_done_callback
        # 会在**当前线程同步**触发 _on_done，而 _on_done 也要拿同一把锁 ——
        # threading.Lock 不可重入，直接死锁，界面表现为"点开始后卡死"。
        #
        # 同理，必须先把本批所有 Future 都登记进 _futures，再统一挂回调，
        # 否则先完成的那个会被 _on_done 判定为"队列已空"，提前触发 on_all_finished。
        submitted: list[tuple[Job, object]] = []
        for job in pending:
            job.reset()
            submitted.append((job, pool.submit(self._execute, job)))

        with self._lock:
            for job, fut in submitted:
                self._futures[job.id] = fut

        for job, fut in submitted:
            fut.add_done_callback(lambda _f, j=job: self._on_done(j))
        return len(pending)

    # ------------------------------------------------------------------ #
    def _decrypt_stage(self, job: Job, report, started: float) -> tuple[bool, Path | None]:
        """加密格式剥壳。返回 (是否继续, 交给引擎的源文件)。

        两种情况：
        * 目标是"原始格式"或恰好等于真实格式 → 直接落盘，任务到此结束；
        * 其它目标 → 先解密到临时文件，后续走正常转码。
        """
        decryptor = decryptor_for_ext(job.src_ext)
        if decryptor is None:
            return True, None

        info = decryptor.probe(job.src)
        err = info.payload.get("error")
        if err:
            job.status = JobStatus.FAILED
            job.message = str(err)
            job.elapsed = time.time() - started
            return False, None

        job.platform = info.platform
        job.real_ext = info.real_ext
        report(0.05, f"{info.platform}格式，正在剥壳")

        real = info.real_ext or (decryptor.outputs[0] if decryptor.outputs else "flac")
        direct = job.dst_ext in (ORIGINAL_EXT, info.real_ext or job.dst_ext)

        if direct:
            target = job.target_for(real)
            if target.exists() and not job.options.overwrite:
                target = unique_path(target)
            job.engine_name = f"{decryptor.label}解密"
            res = decryptor.decrypt(job.src, target, info, report, job.cancel_event)
            job.elapsed = time.time() - started
            if res.cancelled:
                job.status = JobStatus.CANCELLED
                job.message = "已取消"
                _cleanup(target)
            elif res.ok:
                job.status = JobStatus.DONE
                job.progress = 1.0
                job.output = res.output or target
                job.message = "完成"
            else:
                job.status = JobStatus.FAILED
                job.message = res.message or "解密失败"
                _cleanup(target)
            return False, None

        scratch_dir = Path(tempfile.mkdtemp(prefix="fm-dec-"))
        scratch = scratch_dir / f"{job.src.stem}.{real}"
        res = decryptor.decrypt(job.src, scratch, info, report, job.cancel_event)
        if res.cancelled:
            job.status = JobStatus.CANCELLED
            job.message = "已取消"
            shutil.rmtree(scratch_dir, ignore_errors=True)
            return False, None
        if not res.ok:
            job.status = JobStatus.FAILED
            job.message = res.message or "解密失败"
            shutil.rmtree(scratch_dir, ignore_errors=True)
            return False, None
        return True, (res.output or scratch)

    # ------------------------------------------------------------------ #
    def _execute(self, job: Job) -> None:
        job.status = JobStatus.RUNNING
        job.progress = 0.01
        job.message = "准备中"
        if self.on_job_start:
            self.on_job_start(job)

        def report(ratio: float, text: str) -> None:
            job.progress = max(job.progress, min(1.0, ratio))
            job.message = text
            if self.on_job_progress:
                self.on_job_progress(job)

        started = time.time()
        tmpdir: Path | None = None

        try:
            # ---------- 第一步：平台加密格式先剥壳 ---------- #
            src = job.src
            if decryptor_for_ext(job.src_ext) is not None:
                proceed, handed = self._decrypt_stage(job, report, started)
                if not proceed:
                    return
                assert handed is not None
                src = handed
                if src.parent.name.startswith("fm-dec-"):
                    tmpdir = src.parent

            # ---------- 第二步：正常转码 ---------- #
            # 注意：加密源剥壳后真实格式变了，路由要按剥壳后的扩展名来算
            route_src = src.suffix.lstrip(".").lower() or job.src_ext
            engine = self.registry.route(route_src, job.dst_ext)
            if engine is None:
                job.status = JobStatus.FAILED
                job.message = f"没有引擎能完成 {route_src.upper()} → {job.dst_ext.upper()}"
                job.elapsed = time.time() - started
                return
            job.engine_name = engine.name

            target = job.target_path
            if target.exists() and not job.options.overwrite:
                target = unique_path(target)

            try:
                result: ConvertResult = engine.convert(
                    src, target, job.options, report, job.cancel_event
                )
            except Exception as exc:                  # 引擎崩溃不能拖垮整个队列
                result = ConvertResult(False, message=f"引擎异常：{exc}")

            job.elapsed = result.elapsed or (time.time() - started)
            if result.cancelled:
                job.status = JobStatus.CANCELLED
                job.message = "已取消"
                _cleanup(target)
            elif result.ok:
                job.status = JobStatus.DONE
                job.progress = 1.0
                job.output = result.output or target
                job.message = "完成"
            else:
                job.status = JobStatus.FAILED
                job.message = result.message or "失败"
                _cleanup(target)
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.message = f"任务异常：{exc}"
            job.elapsed = time.time() - started
        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)

    def _on_done(self, job: Job) -> None:
        with self._lock:
            self._futures.pop(job.id, None)
            pending = bool(self._futures)
            if not pending:
                self._running = False
        if self.on_job_finish:
            self.on_job_finish(job)
        if not pending and self.on_all_finished:
            self.on_all_finished()

    # ------------------------------------------------------------------ #
    def cancel(self, job: Job) -> None:
        if job.status is JobStatus.RUNNING:
            job.cancel_event.set()
            job.message = "正在取消"
        elif job.status is JobStatus.PENDING:
            job.status = JobStatus.CANCELLED
            job.message = "已取消"
            if self.on_job_finish:
                self.on_job_finish(job)

    def cancel_all(self, jobs: list[Job]) -> None:
        for job in jobs:
            if job.status is JobStatus.PENDING:
                job.cancel_event.set()
            elif job.status is JobStatus.RUNNING:
                job.cancel_event.set()

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        self._running = False


def _cleanup(path: Path) -> None:
    """失败的产物往往是坏文件，直接删掉，避免用户误用。"""
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except OSError:
        pass


def build_job(registry: EngineRegistry, src: Path, target_ext: str, out_dir: Path,
              options: ConvertOptions | None = None) -> Job:
    """工厂函数，顺带做一次可用性预检。"""
    opts = options or ConvertOptions(target_ext=target_ext)
    opts.target_ext = target_ext
    return Job(src=src, options=opts, out_dir=out_dir)


def describe_route(registry: EngineRegistry, src_ext: str, dst_ext: str) -> str:
    """一句话描述这次转换怎么走（对加密格式会说清"先解密"）。"""
    ok, text = registry.explain(src_ext, dst_ext)
    if ok:
        return text
    return f"{label_of(src_ext)} → {label_of(dst_ext)}（无可用引擎）"
