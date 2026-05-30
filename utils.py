"""Utility functions for the Organizer application.

Provides the ``decompose_idea`` function that takes poorly structured user
input (an "idea"), calls an LLM, and returns a structured plan following the
Problem → Task → Subtask hierarchy with tags, time estimates, and priorities.

The LLM judges autonomously whether the input should become a subtask for an
existing task, a new problem, or multiple problems sharing a common topic.
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
   under an existing task; in that case emit ONE problem with ONE task and ONE
   subtask.

2. If the idea describes a self-contained piece of work, emit it as ONE
   **problem** with one or more tasks (each possibly with subtasks).

3. If the idea is broad and touches several independent areas, split it into
   MULTIPLE **problems** that share a common theme (use a shared high-level
   tag to link them).

**For every problem, task, and subtask you MUST provide:**

- ``description`` — a clear, one-sentence description.
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
            "description": "<problem description>",
            "estimated_time": 480,
            "priority": 2,
            "tags": ["tag1"],
            "tasks": [
                {
                    "description": "<task description>",
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

- Every problem MUST have a non-empty ``tasks`` list.
- Every task MAY have an empty ``subtasks`` list.
- ``shared_tags`` are tags that apply across all problems (the common theme).
- ``estimated_time`` is in minutes, always a positive integer.
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

    for prob in data["problems"]:
        if not isinstance(prob, dict):
            continue
        prob.setdefault("description", "Untitled problem")
        prob.setdefault("estimated_time", 0)
        prob.setdefault("priority", 3)
        prob.setdefault("tags", [])
        prob.setdefault("tasks", [])

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

            if not isinstance(task["subtasks"], list):
                task["subtasks"] = []

            for sub in task["subtasks"]:
                if not isinstance(sub, dict):
                    continue
                sub.setdefault("description", "Untitled subtask")
                sub.setdefault("estimated_time", 0)
                sub.setdefault("priority", 3)
                sub.setdefault("tags", [])

    return data


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
    """Decompose a poorly structured user idea into a Problem→Task→Subtask plan.

    The LLM autonomously judges:
    - Whether the input is a single subtask, a new problem, or multiple problems.
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
        raise ValueError("LLM returned an empty problems list")

    # 4. Persist (optional)
    if save:
        os.makedirs(output_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"{output_dir}/plan_{timestamp}.json"
        with open(filename, "w") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        plan["_saved_to"] = filename

    return plan


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
