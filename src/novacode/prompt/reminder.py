"""System reminder helpers for plan mode and execution directives."""


def system_reminder(body: str) -> str:
    return f"<system-reminder>\n{body}\n</system-reminder>"


_PLAN_REMINDER_FULL = """\
PLAN MODE is active. The user indicated that they do not want execution yet.
You MUST NOT make edits, except to the plan file mentioned below.
You MUST NOT run non-readonly tools, change configs, make commits, or otherwise
change the system. These instructions supersede other task instructions.

## Plan File Info:
{plan_file_info}
Build the plan incrementally by writing to or editing this file. This is the
only file you may edit. Other actions must be READ-ONLY.

## Plan Workflow

### Phase 1: Initial Understanding
Goal: Gain a step-by-step understanding of the user's request by reading code
and asking questions.

1. Focus on the user's request and associated code.
2. Search for existing functions, utilities, and reusable patterns.
3. Use explore agents when useful.

### Phase 2: Design
Goal: Design an implementation approach.
Use planning agents when useful, then evaluate the plan yourself.

### Phase 3: Review
Goal: Review the plan and ensure alignment with the user's intent.
Read critical files identified during exploration.

### Phase 4: Final Plan
Goal: Write the final plan to the plan file.
- Begin with context explaining why this change is being made.
- Include only the recommended approach.
- Include the paths of critical files to modify.
- Include a verification section describing how to test the changes.

### Phase 5: Call ExitPlanMode
At the end of the turn, call ExitPlanMode. When the user later sends /do,
execute the accepted plan."""

_PLAN_REMINDER_CONCISE = (
    "PLAN MODE still active. Read-only except plan file ({plan_path}). Follow the 5-phase workflow."
)


def plan_reminder(full: bool) -> str:
    body = _PLAN_REMINDER_FULL if full else _PLAN_REMINDER_CONCISE
    return system_reminder(body)


EXECUTE_DIRECTIVE = "请按上面的计划开始执行。"
