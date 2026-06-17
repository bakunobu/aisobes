# Homepage Relayout — Code Agent Prompt

## Task

Reorganize [`templates/home.html`](templates/home.html) dashboard grid layout. **No backend changes.** Pure CSS/HTML restructure. Keep all existing Jinja2 variables, JS logic, and element IDs intact.

## Target Layout

```
ROW 1 (full width):  📅 WORKFLOW — prominent, stressed
ROW 2 (2 columns):   📋 PROJECTS (left) | ⏱ TIMER → 📊 STATS → 🎯 SESSION → 📜 LOG (right, stacked)
```

## CSS Grid Changes

Replace the `.dashboard` grid:

```css
.dashboard {
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: auto 1fr;
    gap: 16px;
}
```

Remove all `grid-row` inline styles from individual cards. Use semantic placement:
- Workflow: `grid-column: 1 / -1;`
- Projects: `grid-column: 1; grid-row: 2;`
- Right panel wrapper: `grid-column: 2; grid-row: 2;`

## Right Panel

Create a `<div class="right-panel">` wrapper in row 2, column 2 that stacks 4 cards vertically:

```css
.right-panel {
    display: flex;
    flex-direction: column;
    gap: 12px;
}
```

Inside it, place these cards in order:
1. **Timer** — full-size circle (200×200 SVG), task select, controls, today-stats bar
2. **Stats** — compact single-row: `😊 · 3/5 tasks · 1h 20m · ██████░░ 60%`
3. **Session** — compact 2-row form (see below)
4. **Log** — max 4 entries, scrollable, max-height 160px

## Workflow Block (Row 1)

- Full-width card with `grid-column: 1 / -1;`
- Title: "📅 Workflow — What's next?"
- Each task row: checkbox | task label | project name | elapsed time | ▶ start button
- Max 5 visible, scroll if more
- Slightly larger font (14px) and bolder styling to "stress" it

## Projects Block (Row 2, Left)

- Remove `grid-row: 1 / 3` from `.problem-list`
- Set `max-height: 380px; overflow-y: auto;`
- Keep existing project-row structure unchanged
- Compact: show only description, priority badge, task count, Open button

## Timer Block (Row 2, Right — top card)

- Restore original sizing (remove `grid-row: 2` inline style)
- Keep SVG circle, clock display, controls, today-stats bar
- Today-stats bar stays inside timer card as before

## Stats Block (Row 2, Right — 2nd card)

Replace the 2×2 stat grid with a single compact row:

```html
<div class="card stats-compact">
    <div style="display:flex; align-items:center; gap:16px; font-size:13px;">
        <span id="daily-mood" style="font-size:1.3em;">😴</span>
        <span><span id="daily-tasks-count">0/0</span> tasks</span>
        <span><span id="daily-time-spent">0m</span> tracked</span>
        <span style="flex:1;">
            <span id="daily-completion-pct">0%</span>
            <span style="display:inline-block; width:80px; height:6px; background:#e0e0e0; border-radius:3px; margin-left:6px; vertical-align:middle;">
                <span id="daily-completion-fill" style="display:block; height:100%; width:0%; background:#28a745; border-radius:3px;"></span>
            </span>
        </span>
    </div>
</div>
```

Update the JS that populates daily stats to also set `daily-completion-fill.style.width`.

## Session Block (Row 2, Right — 3rd card)

Compact to 2 rows:

```html
<div class="card session-compact">
    <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
        <span>🎯 Create Session</span>
        <button id="session-toggle" style="background:none; border:none; cursor:pointer; font-size:14px;">⚙</button>
    </div>
    <!-- Always visible row -->
    <div style="display:flex; gap:8px; align-items:end;">
        <div style="flex:1;">
            <label style="font-size:11px; color:#666;">Duration (min)</label>
            <input type="number" id="session-duration" value="60" min="15" max="240"
                   style="width:100%; padding:4px; border:1px solid #ccc; border-radius:3px; font-size:12px;">
        </div>
        <div style="flex:1;">
            <label style="font-size:11px; color:#666;">Intensity</label>
            <select id="session-intensity" style="width:100%; padding:4px; border:1px solid #ccc; border-radius:3px; font-size:12px;">
                <option value="low">Low</option>
                <option value="medium" selected>Medium</option>
                <option value="high">High</option>
            </select>
        </div>
        <button id="session-generate" style="padding:4px 12px; background:#007bff; color:#fff; border:none; border-radius:3px; cursor:pointer; font-size:12px; white-space:nowrap;">Generate</button>
    </div>
    <!-- Collapsible advanced row -->
    <div id="session-advanced" style="display:none; margin-top:6px; display:flex; gap:8px;">
        <div style="flex:1;">
            <label style="font-size:11px; color:#666;">Focus</label>
            <select id="session-focus" style="width:100%; padding:4px; border:1px solid #ccc; border-radius:3px; font-size:12px;">
                <option value="">None</option>
                {% for proj in projects %}
                <option value="{{ proj.id }}">{{ proj.description[:30] }}</option>
                {% endfor %}
            </select>
        </div>
        <div style="flex:1;">
            <label style="font-size:11px; color:#666;">Diversity</label>
            <select id="session-diversity" style="width:100%; padding:4px; border:1px solid #ccc; border-radius:3px; font-size:12px;">
                <option value="same">Same domain</option>
                <option value="different">Different</option>
            </select>
        </div>
    </div>
    <!-- Results (hidden until generated) -->
    <div id="session-results" style="display:none; margin-top:8px; max-height:120px; overflow-y:auto;"></div>
</div>
```

Add toggle JS:
```js
document.getElementById('session-toggle').addEventListener('click', () => {
    const adv = document.getElementById('session-advanced');
    adv.style.display = adv.style.display === 'none' ? 'flex' : 'none';
});
```

## Log Block (Row 2, Right — 4th card)

- Compact: `max-height: 160px; overflow-y: auto;`
- Each entry: `[time] [badge] message` on one line
- Font-size: 11px
- Keep existing log-entry structure, just make it more compact

## JS Updates

1. **Daily stats population**: In the existing `fetch('/api/daily-stats')` handler, add:
   ```js
   document.getElementById('daily-completion-fill').style.width = stats.completion_percentage + '%';
   ```

2. **Session toggle**: Add the toggle listener shown above.

3. **Session generate**: Keep existing logic, just ensure element IDs match (`session-duration`, `session-intensity`, `session-focus`, `session-diversity`, `session-generate`, `session-results`, `session-tasks`).

## Rules

- **Minimal changes**: Only modify CSS grid/layout and the HTML structure within `<div class="dashboard">`. Do not touch tabs, `<head>`, or `<script src="/static/timer.js">`.
- **Preserve all IDs**: `timer-clock`, `timer-start`, `timer-pause`, `timer-stop`, `timer-task-select`, `timer-label`, `daily-mood`, `daily-tasks-count`, `daily-time-spent`, `daily-completion-pct`, `daily-completion-bar`
- **No new CSS files**: All styles inline or in the existing `<style>` block.
- **No backend changes**: The `home()` route and all API endpoints stay exactly as-is.
- **Money-effective**: Single file edit, no new dependencies, no additional HTTP requests.