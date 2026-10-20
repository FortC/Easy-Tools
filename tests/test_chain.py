"""事件链引擎测试（方案验收第 5 条）。

关键：用"假动作"把链跑起来，验证变量传递、分支、循环、停止语义，
不去碰真实剪贴板与前台窗口——真实链路由 UI 冒烟测试负责。
"""

from __future__ import annotations

import unittest

from easy_tools.core.actions import base as action_base
from easy_tools.core.actions.base import ActionContext, ActionResult, register
from easy_tools.core.chain import ChainContext, ChainRunner


def _upper_step(ctx, params):
    return ActionResult.success(output=str(params.get("input", "")).upper())


def _suffix_step(ctx, params):
    return ActionResult.success(output=str(params.get("input", "")) + "-done")


if "test.upper" not in action_base.all_actions():
    register("test.upper", "转大写(测试)", "测试用", chain_id="test.upper")(_upper_step)
    register("test.suffix", "加后缀(测试)", "测试用", chain_id="test.suffix")(_suffix_step)


class TestChainContext(unittest.TestCase):
    def test_resolve_step_outputs(self):
        c = ChainContext()
        c.set_step_output("A")
        c.set_step_output("B")
        self.assertEqual(c.resolve("{step1.output}"), "A")
        self.assertEqual(c.resolve("{step2.output}"), "B")
        self.assertEqual(c.resolve("{last.output}"), "B")
        self.assertEqual(c.resolve("{step9.output}"), "B")   # 取不到回退 last

    def test_resolve_locals_and_unknown(self):
        c = ChainContext()
        c.locals["name"] = "张三"
        self.assertEqual(c.resolve("{var:name}"), "张三")
        self.assertEqual(c.resolve("{nope}"), "{nope}")       # 未知变量原样保留

    def test_resolve_none_returns_default(self):
        self.assertEqual(ChainContext().resolve(None, "dflt"), "dflt")


class TestChainRunner(unittest.TestCase):
    def setUp(self):
        self.ctx = ActionContext()
        self.runner = ChainRunner()

    def test_linear_flow(self):
        chain = {"id": "t", "name": "线性", "steps": [
            {"type": "action", "id": "text.strip", "input": "  hi  "},
            {"type": "action", "id": "test.upper", "input": "{step1.output}"},
        ]}
        r = self.runner.run_chain(chain, self.ctx)
        self.assertTrue(r.ok, r.message)
        self.assertEqual(r.output, "HI")
        self.assertEqual(r.steps_run, 2)

    def test_condition_branch_then(self):
        chain = {"id": "t", "name": "分支", "steps": [
            {"type": "action", "id": "text.strip", "input": "a\tb\n1\t2"},
            {"type": "condition", "expr": "has_table", "then": [
                {"type": "action", "id": "table.transpose", "input": "{last.output}"}],
             "else": [{"type": "action", "id": "test.suffix", "input": "{last.output}"}]},
        ]}
        r = self.runner.run_chain(chain, self.ctx)
        self.assertTrue(r.ok, r.message)
        self.assertTrue(r.output.startswith("a\t1"), r.output)

    def test_condition_branch_else(self):
        chain = {"id": "t", "name": "分支2", "steps": [
            {"type": "action", "id": "text.strip", "input": "只有一行"},
            {"type": "condition", "expr": "has_table", "then": [
                {"type": "action", "id": "test.upper", "input": "{last.output}"}],
             "else": [{"type": "action", "id": "test.suffix", "input": "{last.output}"}]},
        ]}
        r = self.runner.run_chain(chain, self.ctx)
        self.assertTrue(r.ok, r.message)
        self.assertEqual(r.output, "只有一行-done")

    def test_loop_and_setvar(self):
        chain = {"id": "t", "name": "循环", "steps": [
            {"type": "setvar", "name": "acc", "value": "x"},
            {"type": "loop", "times": 3, "steps": [
                {"type": "action", "id": "text.strip", "input": "{var:acc}"}]},
        ]}
        r = self.runner.run_chain(chain, self.ctx)
        self.assertTrue(r.ok, r.message)
        self.assertEqual(r.steps_run, 3)

    def test_wait_step(self):
        chain = {"id": "t", "name": "等待", "steps": [
            {"type": "wait", "seconds": 0.01},
            {"type": "action", "id": "text.strip", "input": " ok "},
        ]}
        r = self.runner.run_chain(chain, self.ctx)
        self.assertTrue(r.ok)
        self.assertEqual(r.output, "ok")

    def test_stop_step(self):
        chain = {"id": "t", "name": "中止", "steps": [
            {"type": "action", "id": "text.strip", "input": "a"},
            {"type": "stop", "message": "到此为止"},
            {"type": "action", "id": "test.upper", "input": "{last.output}"},
        ]}
        r = self.runner.run_chain(chain, self.ctx)
        self.assertFalse(r.ok)
        self.assertIn("到此为止", r.message)

    def test_trusted_python_step_passes_output_to_next_step(self):
        chain = {"id": "py", "name": "Python 节点", "steps": [
            {"type": "python", "trusted": True,
             "code": "result = input_text.upper()", "input": "hello"},
            {"type": "action", "id": "text.strip", "input": "{last.output}"},
        ]}
        result = self.runner.run_chain(chain, self.ctx)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.output, "HELLO")

    def test_python_step_requires_explicit_trust(self):
        chain = {"id": "py-untrusted", "name": "未授权脚本", "steps": [
            {"type": "python", "trusted": False, "code": "result = 'should not run'"},
        ]}
        result = self.runner.run_chain(chain, self.ctx)
        self.assertFalse(result.ok)
        self.assertIn("尚未获信任授权", result.message)

    def test_python_step_timeout_terminates_worker(self):
        chain = {"id": "py-timeout", "name": "超时脚本", "steps": [
            {"type": "python", "trusted": True, "timeout_seconds": 1,
             "code": "import time; time.sleep(5)"},
        ]}
        result = self.runner.run_chain(chain, self.ctx)
        self.assertFalse(result.ok)
        self.assertIn("执行超时", result.message)

    def test_failure_propagates(self):
        chain = {"id": "t", "name": "失败", "steps": [
            {"type": "action", "id": "不存在的处理器.xyz", "input": "a"},
        ]}
        r = self.runner.run_chain(chain, self.ctx)
        self.assertFalse(r.ok)

    def test_default_chains_reference_only_known_steps(self):
        """默认配置里的链必须能被解析器识别，否则用户一点就报错。"""
        from easy_tools.config.schema import load_defaults
        from easy_tools.core.actions.processors import all_processors
        cfg = load_defaults()
        known = set(all_processors()) | set(action_base.all_actions())
        for c in cfg["chains"]:
            for step in c["steps"]:
                if step.get("type") == "action":
                    self.assertIn(step["id"], known,
                                  "默认链 %s 引用了未知步骤 id：%s" % (c["id"], step["id"]))

    def test_doc_example_chain_transposes(self):
        """方案 2.5 示例链的去空行 + 转置语义。"""
        chain = {"id": "t", "name": "整理框选表格", "steps": [
            {"type": "action", "id": "text.strip", "input": "  "},
            {"type": "action", "id": "text.drop_blank_lines",
             "input": "a\tb\n\n1\t2"},
            {"type": "condition", "expr": "has_table", "then": [
                {"type": "action", "id": "table.transpose", "input": "{last.output}"}]},
        ]}
        r = self.runner.run_chain(chain, self.ctx)
        self.assertTrue(r.ok, r.message)
        self.assertEqual(r.output, "a\t1\nb\t2\n")


if __name__ == "__main__":
    unittest.main()
