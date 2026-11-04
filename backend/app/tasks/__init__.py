"""任务注册表。新增任务：实现 BaseTask 后在此登记即可，核心调度/日志/报告零改动。"""
from .base import BaseTask, TaskInvalidError
from .cube_task import CubeTask
from .klotski_task import KlotskiTask
from .sokoban_task import SokobanTask

REGISTRY = {
    CubeTask.name: CubeTask,
    KlotskiTask.name: KlotskiTask,
    SokobanTask.name: SokobanTask,
}


def create_task(name: str) -> BaseTask:
    cls = REGISTRY.get(name)
    if not cls:
        raise ValueError(f"未知测评任务: {name}")
    return cls()
