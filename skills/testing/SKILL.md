---
name: testing
description: Test the specific feature/fix implemented in the current session using browser or API tools.
---

# Testing

Tests the specific feature or bug fix implemented in the current session. NOT full E2E testing - focused on the session's work.

## Requirements

- Active session with implemented changes
- Implementation phase completed
- Working Dir available (for starting app)
- If no active session: **STOP and ask user** for session path

## Execution Steps

1. **Read session context:**
   - Read `_overview.md` to understand what was implemented
   - Review implementation docs to identify what needs testing
   - Determine whether this is the post-quality regression run

   **Post-quality regression invariant:** Final Comment Hygiene has already made the
   last allowed project mutation. Before testing, record project `HEAD` and
   `git status --porcelain`. Do not edit, generate, format, or commit project files;
   put reports and screenshots under `SESSION_PATH` and use temporary or user-level
   test configuration. After testing, require the same `HEAD` and status. If either
   changed, do not proceed to PR readiness: document the mutation and return the
   workflow to quality so Code Clarity and final Comment Hygiene run again.

2. **Determine test strategy:**
   - Frontend changes -> Browser testing
   - Backend changes -> API testing via curl/httpie
   - Full-stack -> Both approaches

3. **Select browser testing tool** (if frontend testing needed):

   **Default: `playwright-cli`** — use the `playwright-cli` skill. It handles auth, screenshots, clicks, form fills, route mocking, and extraction via a single CLI. First choice for all E2E work in this environment.

   **Backup: Puppeteer via bash** — use only if `playwright-cli` is unavailable (missing install, broken Chromium, or the scenario specifically needs Puppeteer APIs).

   | Tool | Role | When to use |
   |------|------|-------------|
   | playwright-cli | **Primary** | All E2E: auth flows, clicks, screenshots, network mocking, extraction |
   | Puppeteer (`npx puppeteer`) | Backup | Only if playwright-cli is unavailable or unsuitable |
   | chrome-devtools MCP | Niche | Live console/network inspection when driving a human-operated browser |

   **Recommendation:** Start with `playwright-cli`. Fall back to Puppeteer only with a stated reason.

   **If adding MCP (e.g., chrome-devtools):** After modifying `.mcp.json`, signal `continue` to restart the agent process (MCP doesn't hot-reload).

4. **Start the application from THIS worktree:**
   - Read project's `.samocode` file or README for startup instructions.
   - **If the project uses a single named dev container that is currently mounted from another worktree, stop it and restart from this worktree.** Named dev containers are typically shared across sessions; it is EXPECTED that browser testing may disrupt another session and that is acceptable. Record in the test report which container (if any) was stopped.
   - Verify app is running (ports, health endpoints). If fails to start after two retries, signal blocked.

5. **Execute feature tests:**

   **Browser E2E is MANDATORY when implementation touched frontend files** (any `*.tsx`/`*.ts`/`*.jsx`/`*.js`/`*.css` in the project's frontend directories). Deferring to "human verification" or to a "manual" phase in the plan is NOT allowed — regardless of what the plan labels it.

   Required per FE-touching session:
   - Navigate to every page the feature changes.
   - Exercise the new/modified UI (toggles, filters, forms, empty/error states).
   - Capture screenshots of each key view to `[SESSION_PATH]/_screenshots/[NN]-[view-slug].png`. At minimum: default state, post-interaction state, one edge case.
   - Network audit: record request method + URL + params for the feature's API traffic and include it in the test report. This is how you prove the shipped queries match the intended ones.
   - Console audit: any new browser error or warning introduced by the change blocks the phase.

   **Mock data:** if the feature needs data density (lists, charts, timelines, pagination), seed it via the project's existing scripts or fixtures (check the project README, seed/fixture directories, or `database*/scripts/`). Empty states alone are not sufficient coverage.

   **API testing:**
   ```bash
   # Example: Test endpoint
   curl -X POST http://localhost:8000/api/endpoint \
     -H "Content-Type: application/json" \
     -d '{"test": "data"}'
   ```
   - Use project-specific auth if needed (check .samocode or README)
   - Verify response codes and data

6. **Smoke test (side effect):**
   - App started successfully = smoke test passed
   - No crashes during feature test = smoke test passed

7. **Document results:**

   Create `[SESSION_PATH]/[TIMESTAMP_FILE]-test-report.md`:

   ```markdown
   # Test: [feature name]
   Date: [TIMESTAMP_LOG]
   Run: [1st (post-implementation) | 2nd (post-quality)]
   Result: PASS | FAIL
   Tested HEAD: [full SHA, required for the 2nd run]

   ## What Was Tested
   [Brief description of implemented feature]

   ## Test Environment
   - Working Dir: [path]
   - App Status: [running/failed to start]
   - Testing Tools: [playwright-cli/puppeteer/chrome-devtools/curl]

   ## Test Steps
   1. [Step and result]
   2. [Step and result]
   ...

   ## Results
   - Feature Test: [PASS/FAIL]
   - Smoke Test: [PASS/FAIL]

   ## Issues Found
   [None or list of issues]
   ```

8. **Update session:**
   - Edit `_overview.md`:
     - Flow Log: `- [TIMESTAMP_ITERATION] Feature tested: [result] -> [filename].md`
     - Files: `- [filename].md - Test report`
   - Commit (if git repo): `cd [SESSION_DIR] && git add . && git commit -m "Test: [feature]"`

9. **Signal result:**
    - First-run tests PASS -> signal `continue` to quality
    - Post-quality tests PASS with unchanged HEAD/status -> signal `continue` to
      PR readiness
    - Tests FAIL -> signal `blocked` with failure details (don't auto-fix)
    - Post-quality run changed project HEAD/status -> signal `continue` to quality,
      not PR readiness
    - **Do NOT signal `continue` if mandatory browser E2E was skipped.** If the app could not be brought up after two retries, or if both playwright-cli and Puppeteer are unavailable, signal `blocked` with `needs: "human_decision"` — never defer silently.
    - A project without an automated suite still produces this report and completes
      applicable deterministic checks; lack of a suite never skips the phase.

## Browser Tool Setup

### playwright-cli (primary)

Invoke the `playwright-cli` skill. It handles browser automation via a single CLI — navigate, click, fill, screenshot, extract, and route-mock without writing a driver script. On hosts where the bundled Chromium is missing or unsuitable, configure `.playwright/cli.config.json` with an `executablePath` pointing at a system Chromium and any required launch flags (e.g. `--no-sandbox`).

### Puppeteer (backup)

Use only when playwright-cli is unavailable or the scenario needs Puppeteer-specific APIs (e.g. CDP access patterns that playwright-cli doesn't expose). State the reason for the fallback in the test report.

```bash
# Quick test script
npx puppeteer <<'EOF'
const puppeteer = require('puppeteer');
(async () => {
  const browser = await puppeteer.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('http://localhost:3000');
  // ... test steps
  await browser.close();
})();
EOF
```

### chrome-devtools MCP (niche)

Useful when you need to inspect a live, human-operated session (console, network tab) rather than drive the browser yourself. Add to `.mcp.json`:

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["chrome-devtools-mcp@latest", "--headless=true"]
    }
  }
}
```

Then signal `continue` to restart with new MCP.

## Edge Cases

- Working Dir not in `_overview.md` -> Check project .samocode file for MAIN_REPO, or ask user
- App fails to start -> Document in test report, signal blocked
- playwright-cli unavailable or broken -> Fall back to Puppeteer via bash; state the reason in the test report
- Can't determine what to test -> Review implementation docs, ask if unclear
- No implementation phase completed -> Signal blocked (nothing to test)

## Important Notes

- Test ONLY the session's implemented work
- Don't attempt to fix failures automatically - signal blocked instead
- Document everything for human review
- Smoke test happens naturally (app start + no crashes)
- Read project .samocode file or README for project-specific setup instructions
