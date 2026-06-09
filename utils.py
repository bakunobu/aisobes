"""Utility functions for the Organizer application.

Provides the ``decompose_idea`` function that takes poorly structured user
input (an "idea"), calls an LLM, and returns a structured plan following the
Project → Task → Subtask hierarchy with tags, time estimates, and priorities.

The LLM judges autonomously whether the input should become a subtask for an
existing task, a new project, or multiple projects sharing a common topic.

Also provides ``suggest_merge_tasks`` and ``split_task`` for AI-powered
task management on the project detail page.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import requests
from dotenv import dotenv_values

# ===========================================================================
# Prompt: instruct the LLM to decompose an idea into the Organizer schema
# ===========================================================================

DECOMPOSE_IDEA_PROMPT = """You are an expert task planner.  Given a user's
raw, unstructured idea, produce a structured JSON plan following the schema
below.

**Judgment rules — you decide autonomously:**

1. If the idea is small and specific, it may be a single **subtask** that fits
   under an existing task; in that case emit ONE project with ONE task and ONE
   subtask.

2. If the idea describes a self-contained piece of work, emit it as ONE
   **project** with one or more tasks (each possibly with subtasks).

3. If the idea is broad and touches several independent areas, split it into
   MULTIPLE **projects** that share a common theme (use a shared high-level
   tag to link them).

**For every project, task, and subtask you MUST provide:**

- ``description`` — a clear, one-sentence description that answers
  "How do I know this is done?"  Make the expected result measurable:
  * For coding work: end with a deliverable reference such as
    "— deliverable: a commit with <what the code does> and passing tests".
    Example: "Implement user login endpoint — deliverable: a commit with
    the /login route, session handling, and passing auth tests."
  * For non-coding work: end with a verifiable artifact such as
    "— deliverable: a screenshot of <artifact>" or
    "— deliverable: a photo of <diagram/document>".
    Example: "Design landing page — deliverable: a Figma screenshot of the
    final mockup."
    Example: "Research deployment options — deliverable: a comparison table
    in a shared document."
- ``depends_on`` — OPTIONAL array of 0-based indices.  OMIT or set [] when
  the item can start immediately.
  * On a PROJECT: indices into the parent ``problems`` array — use when this
    project cannot start until another project in this plan is completed.
  * On a TASK: indices into the enclosing ``tasks`` array — use when this
    task needs another task in the same project to finish first (because it
    consumes that task's output).
  * Subtasks do NOT have ``depends_on``.
- ``estimated_time`` — estimated duration in **minutes** (integer).  Try to
  keep subtasks ≤ 120 min.  Think realistically.
- ``priority`` — integer 1 (critical) to 5 (nice-to-have).  Default 3.
- ``tags`` — a list of 1-3 lowercase string tags that categorise the item
  (e.g. ``["backend", "urgent"]``).

**JSON schema — output ONLY this object, no markdown fences, no extra text:**

{
    "shared_tags": ["tag1", "tag2"],
    "problems": [
        {
            "description": "<project description>",
            "depends_on": [],
            "estimated_time": 480,
            "priority": 2,
            "tags": ["tag1"],
            "tasks": [
                {
                    "description": "<task description>",
                    "depends_on": [],
                    "estimated_time": 240,
                    "priority": 2,
                    "tags": ["tag1", "tag2"],
                    "subtasks": [
                        {
                            "description": "<subtask description>",
                            "estimated_time": 120,
                            "priority": 3,
                            "tags": ["tag1"]
                        }
                    ]
                }
            ]
        }
    ]
}

- Every project MUST have a non-empty ``tasks`` list.
- Every task MAY have an empty ``subtasks`` list.
- ``shared_tags`` are tags that apply across all projects (the common theme).
- ``estimated_time`` is in minutes, always a positive integer.
"""

# ===========================================================================
# Prompt: suggest task merges
# ===========================================================================

SUGGEST_MERGE_PROMPT = """You are a task management expert. Given a list of
tasks from the same project, identify pairs of tasks that are similar enough
to be merged into one combined task.

Return ONLY a JSON array of merge suggestions (no markdown fences, no extra text):

[
    {
        "task_a_index": 0,
        "task_b_index": 2,
        "reason": "Both deal with database schema changes",
        "suggested_description": "Refactor and migrate database schema"
    }
]

Rules:
- Only suggest merges when tasks are genuinely overlapping or related.
- If no good merges exist, return an empty array [].
- task_a_index and task_b_index refer to the 0-based position in the input list.
- ``reason`` should explain why the merge makes sense.
- ``suggested_description`` is the proposed combined task description.
"""

# ===========================================================================
# Helper utilities (kept from the original utils.py)
# ===========================================================================


def _load_credentials(credentials: Optional[dict] = None) -> dict:
    """Return credentials, loading from ``.env`` when none are supplied."""
    if credentials is None:
        config = dotenv_values(".env")
        credentials = {
            "api_url": config.get("OPENROUTER_API_URL", ""),
            "api_key": config.get("OPENROUTER_API_KEY", ""),
            "model": config.get("OPENROUTER_API_MODEL", ""),
        }
    required = ["api_url", "api_key", "model"]
    for key in required:
        if not credentials.get(key):
            raise ValueError(f"Missing required credential: {key}")
    return credentials


def _extract_json(text: str) -> str:
    """Best-effort extraction of a JSON substring from LLM output."""
    # Try markdown JSON code fence first
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    # Fall back to the first { … } span
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


def _normalise_decomposed(data: dict) -> dict:
    """Validate and fill defaults on the decomposed LLM response."""
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__}")

    data.setdefault("shared_tags", [])
    data.setdefault("problems", [])

    if not isinstance(data["problems"], list):
        data["problems"] = []

    # Set defaults for depends_on fields
    for prob in data["problems"]:
        if not isinstance(prob, dict):
            continue
        prob.setdefault("description", "Untitled project")
        prob.setdefault("estimated_time", 0)
        prob.setdefault("priority", 3)
        prob.setdefault("tags", [])
        prob.setdefault("tasks", [])
        prob.setdefault("depends_on", [])  # Project-level dependencies

        if not isinstance(prob["tasks"], list):
            prob["tasks"] = []

        for task in prob["tasks"]:
            if not isinstance(task, dict):
                continue
            task.setdefault("description", "Untitled task")
            task.setdefault("estimated_time", 0)
            task.setdefault("priority", 3)
            task.setdefault("tags", [])
            task.setdefault("subtasks", [])
            task.setdefault("depends_on", [])  # Task-level dependencies

            if not isinstance(task["subtasks"], list):
                task["subtasks"] = []

            for sub in task["subtasks"]:
                if not isinstance(sub, dict):
                    continue
                sub.setdefault("description", "Untitled subtask")
                sub.setdefault("estimated_time", 0)
                sub.setdefault("priority", 3)
                sub.setdefault("tags", [])

    # Validate depends_on indices and detect cycles
    _validate_depends_on(data)
    
    return data


def _validate_depends_on(data: dict) -> None:
    """Validate depends_on indices and detect dependency cycles."""
    # Validate project dependencies
    projects = data["problems"]
    project_graph = {}
    
    for i, project in enumerate(projects):
        if not isinstance(project, dict):
            continue
            
        depends_on = project.get("depends_on", [])
        if not isinstance(depends_on, list):
            raise ValueError(f"Project {i}: 'depends_on' must be a list")
            
        for dep_idx in depends_on:
            if not isinstance(dep_idx, int):
                raise ValueError(f"Project {i}: Dependency index must be integer")
            if dep_idx < 0 or dep_idx >= len(projects):
                raise ValueError(f"Project {i}: Invalid dependency index {dep_idx}")
            
        project_graph[i] = depends_on
    
    # Detect cycles in project dependencies
    if _has_cycle(project_graph):
        raise ValueError("Project dependencies contain a cycle")
    
    # Validate task dependencies within each project
    for project in projects:
        if not isinstance(project, dict):
            continue
            
        tasks = project.get("tasks", [])
        task_graph = {}
        
        for j, task in enumerate(tasks):
            if not isinstance(task, dict):
                continue
                
            depends_on = task.get("depends_on", [])
            if not isinstance(depends_on, list):
                raise ValueError(f"Task {j} in project: 'depends_on' must be a list")
                
            for dep_idx in depends_on:
                if not isinstance(dep_idx, int):
                    raise ValueError(f"Task {j}: Dependency index must be integer")
                if dep_idx < 0 or dep_idx >= len(tasks):
                    raise ValueError(f"Task {j}: Invalid dependency index {dep_idx}")
            
            task_graph[j] = depends_on
        
        # Detect cycles in task dependencies
        if _has_cycle(task_graph):
            raise ValueError(f"Task dependencies in project contain a cycle")


def _has_cycle(graph: dict) -> bool:
    """Detect cycles in a dependency graph using DFS."""
    visited = set()
    rec_stack = set()
    
    def dfs(node):
        visited.add(node)
        rec_stack.add(node)
        
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if dfs(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
                
        rec_stack.remove(node)
        return False
    
    for node in graph:
        if node not in visited:
            if dfs(node):
                return True
                
    return False


# ===========================================================================
# Public API
# ===========================================================================


def decompose_idea(
    idea: str,
    prompt: Optional[str] = None,
    credentials: Optional[dict] = None,
    *,
    save: bool = True,
    output_dir: str = "./llm_responses",
    timeout: int = 90,
) -> dict:
    """Decompose a poorly structured user idea into a Project→Task→Subtask plan.

    The LLM autonomously judges:
    - Whether the input is a single subtask, a new project, or multiple projects.
    - Appropriate tags, time estimates (minutes), and priority levels.

    Parameters
    ----------
    idea:
        Raw user input — freeform text, possibly messy.
    prompt:
        Custom system prompt.  Defaults to :data:`DECOMPOSE_IDEA_PROMPT`.
    credentials:
        Dict with keys ``api_url``, ``api_key``, ``model``.  Loaded from
        ``.env`` when ``None``.
    save:
        If ``True`` (default), the plan is saved as a ``.json`` file inside
        *output_dir*.
    output_dir:
        Directory for saved JSON files.  Created if it doesn't exist.
    timeout:
        API request timeout in seconds (default 90).

    Returns
    -------
    dict
        The structured plan:
        {
            "shared_tags": ["..."],
            "problems": [
                {
                    "description": "...",
                    "estimated_time": 480,
                    "priority": 2,
                    "tags": ["..."],
                    "tasks": [
                        {
                            "description": "...",
                            "estimated_time": 240,
                            "priority": 2,
                            "tags": ["..."],
                            "subtasks": [
                                {
                                    "description": "...",
                                    "estimated_time": 120,
                                    "priority": 3,
                                    "tags": ["..."]
                                }
                            ]
                        }
                    ]
                }
            ]
        }

    Raises
    ------
    ValueError
        If credentials are missing, the LLM returns invalid JSON, or the
        response is empty.
    requests.RequestException
        On network or HTTP errors.
    """
    # 1. Resolve credentials
    creds = _load_credentials(credentials)
    system_prompt = prompt if prompt is not None else DECOMPOSE_IDEA_PROMPT

    # 2. Call the LLM
    response = requests.post(
        url=creds["api_url"],
        headers={"Authorization": f"Bearer {creds['api_key']}"},
        json={
            "model": creds["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": idea},
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()

    raw_content = response.json()["choices"][0]["message"]["content"]

    # 3. Parse & normalise
    json_text = _extract_json(raw_content)
    try:
        plan = json.loads(json_text)
    except json.JSONDecodeError:
        raise ValueError(
            f"LLM did not return valid JSON. Raw:\n{raw_content}"
        ) from None

    plan = _normalise_decomposed(plan)

    if not plan.get("problems"):
        raise ValueError("LLM returned an empty projects list")

    # 4. Persist (optional)
    if save:
        os.makedirs(output_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"{output_dir}/plan_{timestamp}.json"
        with open(filename, "w") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        plan["_saved_to"] = filename

    return plan


def suggest_merge_tasks(
    tasks: list,
    prompt: Optional[str] = None,
    credentials: Optional[dict] = None,
    *,
    timeout: int = 90,
) -> list[dict]:
    """Analyze a list of tasks and suggest pairs that can be merged.

    Calls the LLM with the task descriptions/tags and returns a list of
    merge suggestions.  Does NOT mutate the database — the caller decides
    which suggestions to act on.

    Parameters
    ----------
    tasks:
        List of ORM Task instances (must have ``id``, ``tags``, ``priority``,
        ``estimated_time``, and ``subtasks``).
    prompt:
        Custom system prompt.  Defaults to :data:`SUGGEST_MERGE_PROMPT`.
    credentials:
        Dict with keys ``api_url``, ``api_key``, ``model``.
    timeout:
        API request timeout in seconds.

    Returns
    -------
    list[dict]
        Each dict: ``{"task_a_id": int, "task_b_id": int, "reason": str,
        "suggested_description": str}``
    """
    creds = _load_credentials(credentials)
    system_prompt = prompt if prompt is not None else SUGGEST_MERGE_PROMPT

    # Build a compact description of each task
    task_descriptions = []
    task_ids = []
    for t in tasks:
        tags_str = ", ".join(tag.tag for tag in (t.tags or []))
        sub_count = t.subtasks.filter_by(is_deleted=False).count()
        desc = (
            f"Task #{t.id} [priority={t.priority}, "
            f"est={(t.estimated_time or 0) // 60}min, "
            f"tags: {tags_str or 'none'}, subtasks: {sub_count}]"
        )
        task_descriptions.append(desc)
        task_ids.append(t.id)

    user_message = "Tasks in this project:\n" + "\n".join(
        f"{i}. {desc}" for i, desc in enumerate(task_descriptions)
    )

    response = requests.post(
        url=creds["api_url"],
        headers={"Authorization": f"Bearer {creds['api_key']}"},
        json={
            "model": creds["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()

    raw_content = response.json()["choices"][0]["message"]["content"]
    json_text = _extract_json(raw_content)

    try:
        suggestions = json.loads(json_text)
    except json.JSONDecodeError:
        raise ValueError(
            f"LLM did not return valid JSON for merge suggestions. "
            f"Raw:\n{raw_content}"
        ) from None

    if not isinstance(suggestions, list):
        raise ValueError(
            f"Expected a JSON array, got {type(suggestions).__name__}"
        )

    # Map indices back to real task IDs
    result = []
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        a_idx = s.get("task_a_index")
        b_idx = s.get("task_b_index")
        if a_idx is None or b_idx is None:
            continue
        try:
            task_a_id = task_ids[int(a_idx)]
            task_b_id = task_ids[int(b_idx)]
        except (IndexError, ValueError):
            continue
        result.append({
            "task_a_id": task_a_id,
            "task_b_id": task_b_id,
            "reason": s.get("reason", ""),
            "suggested_description": s.get("suggested_description", ""),
        })

    return result


def split_task(
    task_id: int,
    description: str = "",
    hint: str = "",
    prompt: Optional[str] = None,
    credentials: Optional[dict] = None,
    *,
    timeout: int = 90,
) -> list[dict]:
    """Break a coarse task into 2-4 finer-grained tasks via LLM.

    Returns a list of task dicts compatible with the Project→Task→Subtask
    schema.  Each result has ``description``, ``estimated_time`` (minutes),
    ``priority``, ``tags``, and ``subtasks``.

    Parameters
    ----------
    task_id:
        The database ID of the task to split (for logging).
    description:
        Context about the task — passed to the LLM.
    hint:
        Optional hint to guide the split (e.g. "focus on frontend").
    prompt:
        Custom system prompt.
    credentials:
        Dict with ``api_url``, ``api_key``, ``model``.
    timeout:
        API request timeout.

    Returns
    -------
    list[dict]
        Each dict is a task with optional subtasks.
    """
    creds = _load_credentials(credentials)

    SPLIT_TASK_PROMPT = """You are a task decomposition expert. Given a
coarse task, break it into 2-4 finer-grained tasks, each with 1-4 subtasks.

For each item provide: description, estimated_time (minutes), priority (1-5),
tags (1-3 strings).

**Description must be SMART-measurable — answer "How do I know this is done?":**
- Coding tasks: end with "— deliverable: a commit with <result> and passing tests"
- Non-coding tasks: end with "— deliverable: a <screenshot|photo|document> of <artifact>"

- ``depends_on`` — OPTIONAL array of 0-based indices of prerequisite tasks
  in the output array.  Use when a new task cannot start until another new
  task is completed (sequential dependency).  Omit or set [] otherwise.

Output ONLY a JSON array of task objects (no markdown fences):

[
    {
        "description": "<task>",
        "estimated_time": 120,
        "priority": 3,
        "tags": ["tag"],
        "subtasks": [
            {
                "description": "<subtask>",
                "estimated_time": 60,
                "priority": 3,
                "tags": ["tag"]
            }
        ]
    }
]
"""

    system_prompt = prompt if prompt is not None else SPLIT_TASK_PROMPT

    parts = [f"Task to split (ID #{task_id}): {description}"]
    if hint:
        parts.append(f"Hint: {hint}")
    user_message = "\n".join(parts)

    response = requests.post(
        url=creds["api_url"],
        headers={"Authorization": f"Bearer {creds['api_key']}"},
        json={
            "model": creds["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()

    raw_content = response.json()["choices"][0]["message"]["content"]
    json_text = _extract_json(raw_content)

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        raise ValueError(
            f"LLM did not return valid JSON for split. Raw:\n{raw_content}"
        ) from None

    if isinstance(parsed, dict) and "tasks" in parsed:
        parsed = parsed["tasks"]
    if not isinstance(parsed, list):
        raise ValueError(
            f"Expected a JSON array of tasks, got {type(parsed).__name__}"
        )
    if len(parsed) == 0:
        raise ValueError("LLM returned an empty task list for split")

    # Normalise each task
    for task in parsed:
        task.setdefault("description", description or "Untitled task")
        task.setdefault("estimated_time", 0)
        task.setdefault("priority", 3)
        task.setdefault("tags", [])
        task.setdefault("subtasks", [])
        for sub in task.get("subtasks", []):
            sub.setdefault("description", "Untitled subtask")
            sub.setdefault("estimated_time", 0)
            sub.setdefault("priority", 3)
            sub.setdefault("tags", [])

    return parsed


# ===========================================================================
# Legacy functions — kept for backwards compatibility
# ===========================================================================

DEFAULT_TODO_PROMPT = DECOMPOSE_IDEA_PROMPT  # alias for old callers


def generate_todo(
    idea: str,
    prompt: Optional[str] = None,
    credentials: Optional[dict] = None,
    *,
    save: bool = True,
    output_dir: str = "./llm_responses",
    timeout: int = 90,
) -> dict:
    """Legacy wrapper — delegates to :func:`decompose_idea`."""
    return decompose_idea(
        idea,
        prompt=prompt,
        credentials=credentials,
        save=save,
        output_dir=output_dir,
        timeout=timeout,
    )


def refine_task(
    title: str,
    description: str = "",
    hint: str = "",
    *,
    prompt: Optional[str] = None,
    credentials: Optional[dict] = None,
    timeout: int = 90,
) -> list[dict]:
    """Break a coarse task/subtask into finer-grained tasks via LLM.

    Returns a list of task dicts compatible with the Organizer schema.
    """
    creds = _load_credentials(credentials)

    REFINE_TASK_PROMPT = """You are a task decomposition expert. Given a
coarse task, break it into 2-4 finer-grained tasks, each with 1-4 subtasks.

For each item provide: description, estimated_time (minutes), priority (1-5),
tags (1-3 strings).

**Description must be SMART-measurable — answer "How do I know this is done?":**
- Coding tasks: end with "— deliverable: a commit with <result> and passing tests"
- Non-coding tasks: end with "— deliverable: a <screenshot|photo|document> of <artifact>"

Output ONLY a JSON array of task objects (no markdown fences):

[
    {
        "description": "<task>",
        "estimated_time": 120,
        "priority": 3,
        "tags": ["tag"],
        "subtasks": [
            {
                "description": "<subtask>",
                "estimated_time": 60,
                "priority": 3,
                "tags": ["tag"]
            }
        ]
    }
]
"""

    system_prompt = prompt if prompt is not None else REFINE_TASK_PROMPT

    parts = [f"Task to refine: {title}"]
    if description:
        parts.append(f"Description: {description}")
    if hint:
        parts.append(f"Hint: {hint}")
    user_message = "\n".join(parts)

    response = requests.post(
        url=creds["api_url"],
        headers={"Authorization": f"Bearer {creds['api_key']}"},
        json={
            "model": creds["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()

    raw_content = response.json()["choices"][0]["message"]["content"]
    json_text = _extract_json(raw_content)

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        raise ValueError(
            f"LLM did not return valid JSON for refine. Raw:\n{raw_content}"
        ) from None

    if isinstance(parsed, dict) and "tasks" in parsed:
        parsed = parsed["tasks"]
    if not isinstance(parsed, list):
        raise ValueError(
            f"Expected a JSON array of tasks, got {type(parsed).__name__}"
        )
    if len(parsed) == 0:
        raise ValueError("LLM returned an empty task list for refine")

    # Normalise each task
    for task in parsed:
        task.setdefault("description", title)
        task.setdefault("estimated_time", 0)
        task.setdefault("priority", 3)
        task.setdefault("tags", [])
        task.setdefault("subtasks", [])
        for sub in task.get("subtasks", []):
            sub.setdefault("description", "Untitled subtask")
            sub.setdefault("estimated_time", 0)
            sub.setdefault("priority", 3)
            sub.setdefault("tags", [])

    return parsed
