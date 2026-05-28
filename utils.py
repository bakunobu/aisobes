"""Utility functions for the live_interview application.

Provides the generate_todo function that takes a user's idea,
calls an LLM, and returns a structured JSON todo list.
"""

import json
import re
import time
from typing import Optional

import requests
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Default system prompt used to instruct the LLM how to format its response
# ---------------------------------------------------------------------------

DEFAULT_TODO_PROMPT = """You are a structured task planner. Given a user's idea, produce a JSON todo list.

Rules:
1. Write a single-sentence "description" summarising the overall goal.
2. Break the goal into 3-7 main "tasks".
3. Each task may optionally have "subtasks" (0-5 subtasks). Maximum two levels of nesting.
4. Every item (the root, every task, every subtask) MUST have a "status" field whose value is "in_progress".
5. Output ONLY the JSON object. Do NOT wrap it in markdown fences. Do NOT include any other text.

JSON schema:
{
    "description": "<one-sentence summary>",
    "status": "in_progress",
    "tasks": [
        {
            "title": "<task title>",
            "description": "<one-sentence task description>",
            "status": "in_progress",
            "subtasks": [
                {
                    "title": "<subtask title>",
                    "description": "<one-sentence subtask description>",
                    "status": "in_progress"
                }
            ]
        }
    ]
}
"""

REFINE_TASK_PROMPT = """You are a task decomposition expert. Given a task (or subtask) that is too coarse, break it down into 2-4 finer-grained main tasks, each with 1-4 subtasks.

Rules:
1. Each resulting task MUST have a "title", a one-sentence "description", and a "status" of "in_progress".
2. Each task MUST include at least 1 subtask (a "subtasks" list with 1-4 entries). Every subtask also has "title", "description", "status": "in_progress".
3. The breakdown should reflect the user's optional hint if provided.
4. Output ONLY a JSON array of task objects. Do NOT wrap it in markdown fences. Do NOT include any other text.

JSON schema for the array:
[
    {
        "title": "<task title>",
        "description": "<one-sentence description>",
        "status": "in_progress",
        "subtasks": [
            {
                "title": "<subtask title>",
                "description": "<one-sentence subtask description>",
                "status": "in_progress"
            }
        ]
    }
]
"""


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _load_credentials(credentials: Optional[dict] = None) -> dict:
    """Return credentials, loading from ``.env`` when none are supplied.

    Raises ``ValueError`` if any required key is missing.
    """
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
    """Best-effort extraction of a JSON substring from LLM output.

    Handles common wrappers such as ```json … ``` fences.
    """
    # Try to find a markdown JSON code fence first
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)

    # Fall back to the first { … } span
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)

    return text


def _validate_todo_structure(data: dict) -> dict:
    """Ensure the parsed JSON conforms to the expected todo schema.

    Returns the (possibly repaired) dictionary.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__}")

    # Root-level fields
    data.setdefault("description", "No description provided")
    data.setdefault("status", "in_progress")
    data.setdefault("tasks", [])

    if not isinstance(data["tasks"], list):
        data["tasks"] = []

    for task in data["tasks"]:
        if not isinstance(task, dict):
            continue
        task.setdefault("title", "Untitled task")
        task.setdefault("description", "")
        task.setdefault("status", "in_progress")
        task.setdefault("subtasks", [])

        if not isinstance(task["subtasks"], list):
            task["subtasks"] = []

        for subtask in task["subtasks"]:
            if not isinstance(subtask, dict):
                continue
            subtask.setdefault("title", "Untitled subtask")
            subtask.setdefault("description", "")
            subtask.setdefault("status", "in_progress")

    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_todo(
    idea: str,
    prompt: Optional[str] = None,
    credentials: Optional[dict] = None,
    *,
    save: bool = True,
    output_dir: str = "./llm_responses",
    timeout: int = 60,
) -> dict:
    """Generate a structured todo list from a user's idea using an LLM.

    Parameters
    ----------
    idea:
        The user's idea, question, or problem statement to plan.
    prompt:
        Custom system prompt to instruct the LLM.  When ``None`` (the
        default), :data:`DEFAULT_TODO_PROMPT` is used.
    credentials:
        Optional dictionary with keys ``api_url``, ``api_key``, ``model``.
        When ``None``, credentials are loaded from the ``.env`` file.
    save:
        If ``True`` (default), the generated todo is saved as a ``.json``
        file inside *output_dir*.
    output_dir:
        Directory where JSON files are saved.  Created if it does not exist.
    timeout:
        API request timeout in seconds (default 60).

    Returns
    -------
    dict
        The generated todo list with the following structure::

            {
                "description": "...",
                "status": "in_progress",
                "tasks": [
                    {
                        "title": "...",
                        "description": "...",
                        "status": "in_progress",
                        "subtasks": [
                            {
                                "title": "...",
                                "description": "...",
                                "status": "in_progress"
                            }
                        ]
                    }
                ]
            }
    """
    # 1. Resolve credentials -------------------------------------------------
    creds = _load_credentials(credentials)
    system_prompt = prompt if prompt is not None else DEFAULT_TODO_PROMPT

    # 2. Call the LLM --------------------------------------------------------
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

    # 3. Parse & validate ----------------------------------------------------
    json_text = _extract_json(raw_content)
    try:
        todo_data = json.loads(json_text)
    except json.JSONDecodeError:
        raise ValueError(
            f"LLM did not return valid JSON.  Raw response:\n{raw_content}"
        ) from None

    todo_data = _validate_todo_structure(todo_data)

    # 4. Persist to disk (optional) ------------------------------------------
    if save:
        import os

        os.makedirs(output_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"{output_dir}/todo_{timestamp}.json"
        with open(filename, "w") as f:
            json.dump(todo_data, f, indent=2, ensure_ascii=False)
        todo_data["_saved_to"] = filename

    return todo_data


def refine_task(
    title: str,
    description: str = "",
    hint: str = "",
    *,
    prompt: Optional[str] = None,
    credentials: Optional[dict] = None,
    timeout: int = 60,
) -> list[dict]:
    """Break a coarse task/subtask into 2-4 finer-grained main tasks via LLM.

    Parameters
    ----------
    title:
        The title of the task/subtask to refine.
    description:
        The description of the task/subtask being refined.
    hint:
        Optional user instruction to guide the decomposition (e.g. "focus on
        the caching layer").
    prompt:
        Custom system prompt.  Defaults to :data:`REFINE_TASK_PROMPT`.
    credentials:
        Dictionary with keys ``api_url``, ``api_key``, ``model``.
        Loaded from ``.env`` when ``None``.
    timeout:
        API request timeout in seconds (default 60).

    Returns
    -------
    list[dict]
        A list of 2-4 task dicts, each with ``title``, ``description``,
        ``status``, and ``subtasks``, conforming to the application's task
        schema.  Guaranteed to be validated and non-empty.
    """
    creds = _load_credentials(credentials)
    system_prompt = prompt if prompt is not None else REFINE_TASK_PROMPT

    # Build the user message so the LLM knows exactly what to decompose
    parts = [f"Task to refine: {title}"]
    if description:
        parts.append(f"Description: {description}")
    if hint:
        parts.append(f"Hint: {hint}")
    user_message = "\n".join(parts)

    # Call the LLM
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

    # Parse the JSON array response
    json_text = _extract_json(raw_content)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        raise ValueError(
            f"LLM did not return valid JSON for refine. Raw:\n{raw_content}"
        ) from None

    # The LLM should return an array — but sometimes it wraps in an object
    if isinstance(parsed, dict) and "tasks" in parsed:
        parsed = parsed["tasks"]
    if not isinstance(parsed, list):
        raise ValueError(
            f"Expected a JSON array of tasks, got {type(parsed).__name__}"
        )
    if len(parsed) == 0:
        raise ValueError("LLM returned an empty task list for refine")

    # Validate each new task through the same normalisation
    validated = []
    for task in parsed:
        wrapper = {"tasks": [task]}
        wrapper = _validate_todo_structure(wrapper)
        validated.append(wrapper["tasks"][0])

    return validated
