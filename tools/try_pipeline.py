# -*- coding: utf-8 -*-
"""用真实调度器跑一次 kgma -> 目标格式。用法：try_pipeline.py <src> <dst_ext> [out_dir]"""
import sys, os, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.engines.base import ConvertOptions          # noqa
from app.core.jobs import Job, Scheduler, describe_route   # noqa
from app.core.registry import get_registry                 # noqa
from app.core.runtime import ffmpeg_path, ffprobe_path      # noqa

print("ffmpeg:", ffmpeg_path())
print("ffprobe:", ffprobe_path())

src = Path(sys.argv[1])
dst_ext = sys.argv[2]
out_dir = Path(sys.argv[3] if len(sys.argv) > 3 else "tests/_work/out")
out_dir.mkdir(parents=True, exist_ok=True)

reg = get_registry()
print("路由说明:", describe_route(reg, src.suffix.lstrip("."), dst_ext))

opts = ConvertOptions(target_ext=dst_ext, overwrite=True)
job = Job(src=src, options=opts, out_dir=out_dir)

sched = Scheduler(reg, max_workers=1)
sched.on_job_start = lambda j: print("[start]", j.src.name, "->", j.dst_ext)
sched.on_job_progress = lambda j: None
sched.on_job_finish = lambda j: print("[finish]", j.status.value, "|", j.message,
                                      "| engine=", j.engine_name,
                                      "| real=", j.real_ext)
sched.run([job])
while sched.running:
    time.sleep(0.2)

print("=" * 60)
print("状态:", job.status.value, "| 说明:", job.message, "| 耗时: %.1fs" % job.elapsed)
print("产物:", job.output, (job.output.stat().st_size if job.output and job.output.exists() else "-"))
if job.output and job.output.exists():
    print("前 32 字节:", job.output.read_bytes()[:32].hex(" "))
