"""测评任务插件接口。新增任务只需实现 BaseTask 并在 config.TASKS 登记。"""


class TaskInvalidError(Exception):
    """模型输出无法解析为合法动作。"""


class BaseTask:
    name = ""
    label = ""

    def create_case(self, idx: int, seed: int, params: dict) -> dict:
        """生成一个测试用例（含随机种子，保证可复现）。返回 dict，含内部状态。"""
        raise NotImplementedError

    def build_messages(self, case: dict) -> list:
        """生成初始对话消息。"""
        raise NotImplementedError

    def tools_spec(self) -> list:
        """任务提供的工具定义（OpenAI tools 格式）。"""
        raise NotImplementedError

    def execute_tool(self, case: dict, name: str, arguments: str) -> dict:
        """执行模型发起的工具调用，返回工具结果（将回传给模型）。"""
        raise NotImplementedError

    def handle_final_text(self, case: dict, text: str) -> dict:
        """处理模型的无工具文本回复。返回 {status: solved/invalid/continue, feedback?, ...}"""
        raise NotImplementedError

    def is_solved(self, case: dict) -> bool:
        raise NotImplementedError

    def score(self, case: dict, solved: bool) -> dict:
        """终局评分（任务自定义指标）。"""
        raise NotImplementedError

    def solution_for(self, case: dict) -> str:
        """供 Mock 网关使用的参考解法。"""
        raise NotImplementedError
