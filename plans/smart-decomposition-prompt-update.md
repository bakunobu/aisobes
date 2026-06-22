# Plan: SMART-Oriented Decomposition Prompt Update

## Goal

Enhance the `description` field guidance in the LLM decomposition prompts to be more **SMART**-oriented, with special emphasis on **Measurability**. The key principle: every subtask/task description should imply a concrete, verifiable expected result — something you could point at and say "done."

## What Changes

No new JSON fields. Only the prompt text (instructions to the LLM) changes. The JSON schema stays identical.

## Which Prompts Are Updated

| Prompt | File:Line | Reason |
|--------|-----------|--------|
| `DECOMPOSE_IDEA_PROMPT` | [`utils.py:29`](utils.py:29) | Primary decomposition flow — generates full Project→Task→Subtask plans |
| `SPLIT_TASK_PROMPT` | [`utils.py:465`](utils.py:465) | AI-powered task splitting on project detail page |
| `REFINE_TASK_PROMPT` | [`utils.py:589`](utils.py:589) | Legacy task refinement helper |

`SUGGEST_MERGE_PROMPT` is intentionally skipped — it deals with pairing existing tasks, not generating new descriptions.

## SMART Guidance to Inject

For every `description` field, the prompt will now instruct the LLM to ensure it answers: **"How do I know this is done?"**

### For Coding-Related Tasks/Subtasks

Descriptions should end with a concrete deliverable reference:

```
❌ "Set up the database schema"
✅ "Set up the database schema — deliverable: a commit with the migration file and passing model tests"

❌ "Add user authentication"
✅ "Add user authentication — deliverable: a commit with login endpoint, session middleware, and a green test suite"

❌ "Write API endpoint for tasks"
✅ "Write API endpoint for tasks — deliverable: a commit with the route handler, request validation, and integration tests"
```

### For Non-Coding Tasks/Subtasks

Descriptions should reference something verifiable — a screenshot, a photo, a document:

```
❌ "Design the landing page"
✅ "Design the landing page — deliverable: a Figma screenshot of the final mockup"

❌ "Research deployment options"
✅ "Research deployment options — deliverable: a comparison table in a shared document"

❌ "Plan the database architecture"
✅ "Plan the database architecture — deliverable: a photo of the whiteboard ERD diagram"
```

## Before/After: `DECOMPOSE_IDEA_PROMPT`

### Current (lines 29-89)

```python
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

- ``description`` — a clear, one-sentence description.
- ``estimated_time`` — estimated duration in **minutes** (integer).  Try to
  keep subtasks ≤ 120 min.  Think realistically.
- ``priority`` — integer 1 (critical) to 5 (nice-to-have).  Default 3.
- ``tags`` — a list of 1-3 lowercase string tags that categorise the item
  (e.g. ``["backend", "urgent"]``).

...
"""
```

### Proposed

```python
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
- ``estimated_time`` — estimated duration in **minutes** (integer).  Try to
  keep subtasks ≤ 120 min.  Think realistically.
- ``priority`` — integer 1 (critical) to 5 (nice-to-have).  Default 3.
- ``tags`` — a list of 1-3 lowercase string tags that categorise the item
  (e.g. ``["backend", "urgent"]``).

...
"""
```

## Before/After: `SPLIT_TASK_PROMPT`

### Current (lines 465-489)

```python
SPLIT_TASK_PROMPT = """You are a task decomposition expert. Given a
coarse task, break it into 2-4 finer-grained tasks, each with 1-4 subtasks.

For each item provide: description, estimated_time (minutes), priority (1-5),
tags (1-3 strings).

Output ONLY a JSON array of task objects (no markdown fences):
...
"""
```

### Proposed

```python
SPLIT_TASK_PROMPT = """You are a task decomposition expert. Given a
coarse task, break it into 2-4 finer-grained tasks, each with 1-4 subtasks.

For each item provide: description, estimated_time (minutes), priority (1-5),
tags (1-3 strings).

**Description must be SMART-measurable — answer "How do I know this is done?":**
- Coding tasks: end with "— deliverable: a commit with <result> and passing tests"
- Non-coding tasks: end with "— deliverable: a <screenshot|photo|document> of <artifact>"

Output ONLY a JSON array of task objects (no markdown fences):
...
"""
```

## Before/After: `REFINE_TASK_PROMPT`

### Current (lines 589-613)

Same structure as `SPLIT_TASK_PROMPT` — same update pattern.

### Proposed

Identical SMART guidance block as `SPLIT_TASK_PROMPT`.

## Implementation Checklist

1. **Update `DECOMPOSE_IDEA_PROMPT`** — Replace the `description` bullet with SMART guidance
2. **Update `SPLIT_TASK_PROMPT`** — Add SMART description block before the JSON schema
3. **Update `REFINE_TASK_PROMPT`** — Add identical SMART description block
4. **Consistency review** — Verify all three prompts use identical wording for the SMART guidance block

## What Does NOT Change

- JSON schema (no new fields like `expected_result`)
- `_normalise_decomposed()` function
- `_save_plan_to_db()` function
- Any template files
- Database models
- `SUGGEST_MERGE_PROMPT`
