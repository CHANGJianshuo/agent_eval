"""多轮对话主循环:用户模拟器(骑手) ↔ SUT(站长),全程落 trace。"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from ..models.persona import Persona
from ..models.task import TaskDefinition
from ..models.trace import TraceMessage
from ..user_simulator.simulator import UserSimulator
from .sut_adapter import SUTAdapter
from .trace_io import TraceWriter


def run_dialogue(task: TaskDefinition, persona: Persona,
                 sut_model: str, simulator_model: str,
                 trace_path: str | Path,
                 sut_temperature: float = 0.7,
                 simulator_temperature: float = 0.7,
                 sut_reasoning_effort: str | None = None,
                 simulator_reasoning_effort: str | None = None,
                 simulator_seed: int = 0) -> Path:
    """跑一通电话,trace 落盘,返回 trace 路径。

    外呼接通后由 SUT 主动说开场白；之后每轮为用户回应 → SUT 回应。
    用户走到终止状态后,SUT 仍再回应最后一句(用于捕捉「安抚后挂断」),
    然后结束。

    simulator_seed:用户模拟器掷骰(噪音命中)用的种子,保证可复现。
    """
    trace_path = Path(trace_path)
    sut = SUTAdapter(sut_model, task.rendered_prompt(),
                     sut_temperature, sut_reasoning_effort)
    simulator = UserSimulator(simulator_model, persona,
                              simulator_temperature, simulator_reasoning_effort,
                              seed=simulator_seed)

    messages: list[TraceMessage] = []
    turn = 0
    end_reason = "max_rounds"

    with TraceWriter(trace_path) as tw:
        tw.write({
            "event": "dialogue_start",
            "trace_id": uuid.uuid4().hex,
            "task_id": task.task_id,
            "persona_id": persona.id,
            "sut_model": sut_model,
            "simulator_model": simulator_model,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })

        try:
            # 这是外呼场景。若让模拟用户先说，Opening Line 永远不会进入
            # trace，opening rubric 也会系统性得到 0 分。
            turn += 1
            opening_text = sut.open_call()
            opening = TraceMessage(
                turn=turn, role="assistant", text=opening_text,
            )
            messages.append(opening)
            tw.write({"event": "turn", **opening.model_dump()})

            max_rounds = min(persona.max_rounds, task.max_rounds)
            for _ in range(max_rounds):
                # --- 骑手(用户模拟器)说 ---
                turn += 1
                user_text, state, done, probe_id = simulator.next(messages)
                um = TraceMessage(turn=turn, role="user", text=user_text,
                                  state=state, is_probe=probe_id is not None,
                                  probe_id=probe_id)
                messages.append(um)
                tw.write({"event": "turn", **um.model_dump()})

                # --- 站长(SUT)回应 ---
                turn += 1
                sut_text = sut.respond(messages)
                am = TraceMessage(turn=turn, role="assistant", text=sut_text)
                messages.append(am)
                tw.write({"event": "turn", **am.model_dump()})

                if done:
                    end_reason = "done"
                    break
        except Exception as exc:
            # Keep failed traces structurally complete so report/migration code
            # can distinguish a failed call from a process killed mid-write.
            tw.write({
                "event": "dialogue_error",
                "error_type": type(exc).__name__,
                "message": str(exc)[:500],
            })
            tw.write({
                "event": "dialogue_end",
                "turn_count": turn,
                "end_reason": "error",
            })
            raise
        else:
            tw.write({"event": "dialogue_end", "turn_count": turn,
                      "end_reason": end_reason})

    return trace_path
