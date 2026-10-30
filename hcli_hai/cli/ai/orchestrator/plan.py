import json
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Step:
    status: str = ""
    goal: str = ""
    why: str = ""
    bash: str = ""
    say: str = ""



@dataclass
class PlanState:
    run_id: str = ""
    goal: str = ""
    plan_status: str = "idle"
    step: Step = field(default_factory=Step)
    observations: List[Dict[str, str]] = field(default_factory=list)
    say: str = ""



class Plan:
    """Thread-safe singleton holding the current harness state.
    Bare-bones: goal, current step, and flat observation history. No task graph.
    """

    _instance: Optional["Plan"] = None
    _lock = threading.RLock()

    def __new__(cls) -> "Plan":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        with self._lock:
            if hasattr(self, "_initialized"):
                return
            self._state = PlanState()
            self._initialized = True

    def clear(self) -> None:
        with self._lock:
            self._state = PlanState()

    def replace(self, state: Dict[str, Any]) -> None:
        with self._lock:
            self._state = self._from_dict(state)

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._to_dict(self._state)

    def public(self) -> Dict[str, Any]:
        with self._lock:
            step = self._state.step
            status = step.status or ""
            bash = step.bash if status == "continue" else ""
            say = self._state.say or step.say or ""
            if status == "continue":
                say = ""
            return {
                "run_id": self._state.run_id,
                "status": status,
                "goal": self._state.goal,
                "say": say,
                "plan_status": self._state.plan_status,
                "observations": list(self._state.observations),
                "step": {
                    "status": status,
                    "goal": step.goal,
                    "why": step.why,
                    "bash": bash,
                    "say": say,
                },
            }

    def dumps(self) -> str:
        with self._lock:
            return json.dumps(self.public(), ensure_ascii=False, indent=4)

    def get_step(self) -> Dict[str, Any]:
        return asdict(self._state.step)

    def set_step(self, step: Dict[str, Any]) -> None:
        with self._lock:
            self._state.step = Step(**{k: v for k, v in step.items() if k in Step.__dataclass_fields__})

    def clear_step(self) -> None:
        with self._lock:
            self._state.step = Step()

    def append_observation(self, bash: str, result: str) -> None:
        """Always append a new observation. Never overwrite prior results for the same command."""
        with self._lock:
            bash = " ".join((bash or "").split())
            result = result or ""
            self._state.observations.append({"bash": bash, "result": result})

    def has_observation(self, bash: str) -> bool:
        with self._lock:
            bash = " ".join((bash or "").split())
            for obs in self._state.observations:
                if " ".join(obs.get("bash", "").split()) == bash:
                    return True
            return False

    def _from_dict(self, data: Dict[str, Any]) -> PlanState:
        state = PlanState(
            run_id=data.get("run_id", ""),
            goal=data.get("goal", ""),
            plan_status=data.get("plan_status", "idle"),
            say=data.get("say", ""),
            observations=data.get("observations", []),
        )
        step_data = data.get("step", {})
        state.step = Step(
            status=step_data.get("status", ""),
            goal=step_data.get("goal", ""),
            why=step_data.get("why", ""),
            bash=step_data.get("bash", ""),
            say=step_data.get("say", ""),
        )
        return state

    def _to_dict(self, state: PlanState) -> Dict[str, Any]:
        return {
            "run_id": state.run_id,
            "goal": state.goal,
            "plan_status": state.plan_status,
            "step": asdict(state.step),
            "observations": list(state.observations),
            "say": state.say,
        }

    def set_run_id(self, run_id: str) -> None:
        with self._lock:
            self._state.run_id = run_id or ""

