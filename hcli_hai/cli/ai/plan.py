import json
import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Step:
    """Represents the current step the orchestrator is waiting on."""
    id: str = ""
    status: str = ""
    goal: str = ""
    why: str = ""
    bash: str = ""
    say: str = ""
    claimed: bool = False


@dataclass
class Task:
    """A single task in the plan."""
    id: str
    intent: str
    acceptance: str = ""
    depends_on: List[str] = field(default_factory=list)
    status: str = "pending"
    result: str = ""
    observations: List[Dict[str, str]] = field(default_factory=list)
    hint: str = ""
    check: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanState:
    """Internal representation of the full plan."""
    goal: str = ""
    plan_status: str = "idle"
    cursor: str = ""
    tasks: List[Task] = field(default_factory=list)
    step: Step = field(default_factory=Step)
    say: str = ""
    replans: int = 0
    skill_area: str = ""
    skill: str = ""
    step_seq: int = 0


class Plan:
    """
    Thread-safe singleton for managing the orchestrator's execution plan.

    This class is responsible only for storing and safely exposing plan state.
    All orchestration logic (planning, running tasks, replanning, etc.) should
    live outside this class.
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

    # ------------------------------------------------------------------
    # Core State Management
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset the plan to an empty initial state."""
        with self._lock:
            self._state = PlanState()

    def replace(self, state: Dict[str, Any]) -> None:
        """
        Replace the entire plan state with a new one.
        This is the primary way to install a new plan (from planning or replanning).
        """
        with self._lock:
            self._state = self._from_dict(state)

    def get_state(self) -> Dict[str, Any]:
        """Return a deep copy of the full internal plan state."""
        with self._lock:
            return self._to_dict(self._state)

    # ------------------------------------------------------------------
    # Public / Client-Facing View
    # ------------------------------------------------------------------

    def public(self) -> Dict[str, Any]:
        """
        Return a sanitized view of the plan suitable for external clients.

        This is what `hai agent plan` and similar commands should expose.
        """
        with self._lock:
            step = self._state.step
            status = step.status or ""

            bash = step.bash if status == "continue" else ""
            say = self._state.say or step.say or ""
            if status == "continue":
                say = ""

            return {
                "status": status,
                "goal": self._state.goal,
                "why": step.why,
                "bash": bash,
                "say": say,
                "plan_status": self._state.plan_status,
                "cursor": self._state.cursor,
                "tasks": [self._task_to_public(t) for t in self._state.tasks],
                "step": {
                    "status": status,
                    "goal": step.goal,
                    "why": step.why,
                    "bash": bash,
                    "say": say,
                },
            }

    def dumps(self) -> str:
        """Return JSON of the public state."""
        with self._lock:
            return json.dumps(self.public(), ensure_ascii=False, indent=4)

    # ------------------------------------------------------------------
    # Step Management
    # ------------------------------------------------------------------

    def get_step(self) -> Dict[str, Any]:
        """Return the current step as a dictionary."""
        return asdict(self._state.step)

    def set_step(self, step: Dict[str, Any]) -> None:
        """Set the current step (used when emitting a new bash command)."""
        with self._lock:
            self._state.step = Step(**{k: v for k, v in step.items() if k in Step.__dataclass_fields__})

    def clear_step(self) -> None:
        """Clear the current step."""
        with self._lock:
            self._state.step = Step()

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def append_observation(self, bash: str, result: str) -> None:
        """
        Append or update an observation for the current cursor task.
        """
        with self._lock:
            cursor = self._state.cursor
            target = self._find_task(cursor) or (self._state.tasks[-1] if self._state.tasks else None)
            if target is None:
                return

            bash = bash or ""
            for obs in target.observations:
                if obs.get("bash") == bash:
                    obs["result"] = result or ""
                    return

            target.observations.append({"bash": bash, "result": result or ""})

    def has_observation(self, bash: str) -> bool:
        """Check if the current task already has an observation for this bash command."""
        with self._lock:
            cursor = self._state.cursor
            for task in self._state.tasks:
                if task.id != cursor:
                    continue
                for obs in task.observations:
                    if obs.get("bash", "").strip() == bash.strip():
                        return True
            return False

    # ------------------------------------------------------------------
    # Convenience Accessors (used by orchestrator)
    # ------------------------------------------------------------------

    def get_cursor_task(self) -> Optional[Dict[str, Any]]:
        """Return the task currently pointed to by cursor, if any."""
        with self._lock:
            task = self._find_task(self._state.cursor)
            return asdict(task) if task else None

    def update_task(self, task_id: str, **updates) -> bool:
        """Update fields on a specific task."""
        with self._lock:
            task = self._find_task(task_id)
            if not task:
                return False
            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            return True

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _find_task(self, task_id: str) -> Optional[Task]:
        for task in self._state.tasks:
            if task.id == task_id:
                return task
        return None

    def _task_to_public(self, task: Task) -> Dict[str, Any]:
        obs = []
        for item in task.observations:
            result = item.get("result", "")
            obs.append({"bash": item.get("bash", ""), "result": result})
        return {
            "id": task.id,
            "intent": task.intent,
            "acceptance": task.acceptance,
            "depends_on": task.depends_on,
            "status": task.status,
            "result": task.result,
            "observations": obs,
            "hint": task.hint,
            "check": task.check,
        }

    def _from_dict(self, data: Dict[str, Any]) -> PlanState:
        state = PlanState(
            goal=data.get("goal", ""),
            plan_status=data.get("plan_status", "idle"),
            cursor=data.get("cursor", ""),
            say=data.get("say", ""),
            replans=data.get("replans", 0),
            skill_area=data.get("skill_area", ""),
            skill=data.get("skill", ""),
            step_seq=data.get("step_seq", 0),
        )

        # Tasks
        for t in data.get("tasks", []):
            state.tasks.append(Task(
                id=t.get("id", ""),
                intent=t.get("intent", ""),
                acceptance=t.get("acceptance", ""),
                depends_on=t.get("depends_on", []),
                status=t.get("status", "pending"),
                result=t.get("result", ""),
                observations=t.get("observations", []),
                hint=t.get("hint", ""),
                check=t.get("check", {}),
            ))

        # Step
        step_data = data.get("step", {})
        state.step = Step(
            status=step_data.get("status", ""),
            goal=step_data.get("goal", ""),
            why=step_data.get("why", ""),
            bash=step_data.get("bash", ""),
            say=step_data.get("say", ""),
            id=step_data.get("id", ""),
            claimed=step_data.get("claimed", False),
        )

        return state

    def _to_dict(self, state: PlanState) -> Dict[str, Any]:
        return {
            "goal": state.goal,
            "plan_status": state.plan_status,
            "cursor": state.cursor,
            "tasks": [asdict(t) for t in state.tasks],
            "step": asdict(state.step),
            "say": state.say,
            "replans": state.replans,
            "skill_area": state.skill_area,
            "skill": state.skill,
            "step_seq": state.step_seq,
        }
