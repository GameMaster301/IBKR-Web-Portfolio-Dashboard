# Code Review: IBKR Web Portfolio Dashboard

**Repository:** [GameMaster301/IBKR-Web-Portfolio-Dashboard](https://github.com/GameMaster301/IBKR-Web-Portfolio-Dashboard)  
**Review Date:** April 24, 2026  
**Total Lines of Code:** ~7,000 (Python) + CSS, YAML, shell scripts  
**Commit History:** 11 commits over ~24 days (March 31 – April 24, 2026)

---

## Status Update — April 24, 2026 (post-review)

All roadmap items and most review items have been addressed since this review was written.

| Review deduction | Status |
|---|---|
| -2 documentation drift (scenario count) | ✅ Fixed — README + CLAUDE.md updated to 5 scenarios |
| -1 EUR/USD fallback scattered | ✅ Fixed — single `cfg['display']['eurusd_fallback']` reference; `EURUSD_FALLBACK` in helpers.py |
| -4 no automated tests | ✅ Fixed — smoke_test.py (~50 checks, <1s, no network/IB) |
| -2 no linting | ✅ Fixed — pyproject.toml (ruff), CI checks.yml |
| -1 no pinned dependencies | ✅ Fixed — requirements.txt pinned to known-good ranges |
| -5 dashboard.py monolith (3,417 lines) | ✅ Fixed — split into dashboard_core/ (9 modules); dashboard.py is now ~30 lines |
| -2 layout/callbacks interleaved | ✅ Fixed — layout in dashboard_core/layout.py, callbacks in separate modules |
| -1 globals undocumented | 🔲 Open — _EVER_CONNECTED and _APP_START still not in CLAUDE.md |
| -1 type annotation coverage | ✅ Partially fixed — schemas.py TypedDicts + annotated callback signatures |
| -1 magic numbers in styles | ⚠️ Partial — styles.py exists; inline literals still present in some callbacks |
| -1 mobile responsiveness | 🔲 Open — planned as multi-currency/UX phase |

Additional improvements not in the original review:
- `decorators.py`: `safe_render` / `NotReadyError` — standardised error/loading UX across all panels
- `net_util.py`: unified retry + parallel fetch helpers
- `cache_util.py`: disk-backed single-flight caching (diskcache)
- EUR-primary currency display is intentional; multi-currency support is planned as a future feature

**Revised estimated grade: A- (~90/100)**

---

## Overall Grade: **B+ (83/100)**

This is a genuinely useful, well-conceived personal finance tool that punches above its weight for a solo hobby project. It demonstrates strong engineering instincts — hardened async networking, thoughtful caching, Docker-first distribution, and a surprisingly rich feature set. The primary deductions come from a single architectural bottleneck (a 3,400-line monolithic UI module), the complete absence of automated tests, and a handful of documentation inconsistencies. With targeted refactoring and a minimal test harness, this project could easily reach A-tier.

---

## Scoring Breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Features & Capabilities | 22/25 | 25% | 22.0 |
| Architecture & Design | 17/25 | 25% | 17.0 |
| Code Quality & Craft | 18/25 | 25% | 18.0 |
| Organization & Documentation | 14/15 | 15% | 14.0 |
| DevOps & Distribution | 9/10 | 10% | 9.0 |
| **Total** | — | 100% | **80/100 → B+** |

> Scores are adjusted upward slightly for the hobbyist context and the project's rapid development pace (11 commits, 24 days).

---

## 1. Features & Capabilities — 22/25

### What Works Well

The feature breadth is impressive for a personal project. The dashboard delivers a coherent, production-grade experience across several independent domains simultaneously.

**Live portfolio data** is fetched directly from IB Gateway or TWS via `ib_async`, with no reliance on third-party data vendors. The `_do_fetch()` coroutine runs five parallel async chains — positions, dividends, EUR/USD rate, daily P&L, and recent trade fills — using `asyncio.gather`, which cuts total fetch latency to the maximum of the individual chains rather than their sum.

**Market intelligence** (sector/geography exposure, earnings calendar with historical 1-day post-earnings moves) and **macro valuation** (Buffett Indicator via Wilshire 5000/FRED GDP, Shiller CAPE with 50-year history, S&P 500 trailing P/E, 10-year Treasury yield) are sourced from yfinance and public web scrapers, cached for four hours via `diskcache`, and degrade gracefully when network calls fail.

**The Portfolio Coach** is a thoughtful two-tier design: a rules-based offline tier (`coach.py`) that answers five scenario questions using only live portfolio data, and an optional LLM tier (`ai_provider.py`) that supports Anthropic, xAI Grok, and OpenAI via BYO key stored exclusively in browser `localStorage`. The privacy model — API keys never leave the browser, never touch the server — is sound and clearly communicated.

**Demo mode** is a first-class feature: a deterministic mock payload (`demo_data.py`) allows the full dashboard to be explored without a live IBKR connection, which dramatically lowers the barrier to evaluation and contribution.

**Distribution** is polished: one-liner installers for Windows (PowerShell) and Mac/Linux (curl/bash), Docker-first with multi-platform images (amd64 + arm64), and a GitHub Release zip containing everything a non-developer needs.

### Deductions

**~~-2: Documentation drift on scenario count.~~** ✅ Fixed — README and CLAUDE.md updated to reflect 5 scenarios.

**~~-1: EUR-centric hardcoding.~~** ✅ Fixed — `1.08` consolidated to `cfg['display']['eurusd_fallback']`; `EURUSD_FALLBACK` constant in `dashboard_core/helpers.py` is the single reference point for all callbacks. EUR-primary display is intentional; multi-currency support is planned as a future feature.

**-1: No mobile responsiveness.** The layout uses fixed pixel widths (e.g., `'width': '260px'` for the donut chart, `'gridTemplateColumns': 'repeat(4, 1fr)'` for summary cards) and no responsive breakpoints. On a phone or narrow viewport the dashboard breaks. A simple CSS media query or Dash Bootstrap Components integration would resolve this.

---

## 2. Architecture & Design — 17/25

### What Works Well

The **threading model** is well-designed and correctly documented in `CLAUDE.md`. A single daemon thread owns the `ib_async` event loop; Dash callbacks cross into it exclusively via `asyncio.run_coroutine_threadsafe`. The `_fetch_lock` prevents overlapping fetches without blocking the Dash worker pool. The `request_retry()` mechanism uses `loop.call_soon_threadsafe` to wake the reconnect loop immediately, bypassing exponential back-off — a clean, correct pattern.

The **`dcc.Store` as message bus** pattern is idiomatic Dash. `portfolio-data` is the single source of truth; all rendering callbacks are pure functions of store state. This makes the data flow easy to reason about and test in isolation.

**`net_util.py`** is a positive sign of architectural maturity: it was explicitly introduced to extract duplicated retry/concurrency logic from `analytics`, `market_intel`, `market_valuation`, and `dashboard.py`. The three helpers (`fetch_with_retry`, `fetch_parallel`, `run_parallel`) are generic, well-documented, and reused consistently.

The **two-stage Dockerfile** (builder + slim runtime), non-root `appuser`, and HTTP healthcheck reflect solid container hygiene.

### Deductions

**~~-5: `dashboard.py` is a 3,417-line monolith.~~** ✅ Fixed — see status table above.

**[original text preserved below]** **`dashboard.py` was a 3,417-line monolith.** This is the most significant architectural problem in the repository. The file contains 74 function definitions and 40 registered callbacks covering holdings rendering, summary cards, the donut chart, position detail panel, market intelligence, market valuation, dividends, trade history, PDF export, the AI coach, keyboard shortcuts, toast notifications, and demo/retry mode toggles. The author acknowledges this in the file's own docstring ("A split into `dashboard_core/` submodules is planned") and in `CLAUDE.md`, but the split has not happened. At this size, the file is difficult to navigate, impossible to test in isolation, and creates merge conflicts in any collaborative scenario.

**-2: No separation between layout and callbacks.** In the Dash ecosystem, it is common practice to separate layout definitions from callback logic, either into distinct files or at minimum into clearly demarcated sections. In `dashboard.py`, layout HTML and callback functions are interleaved, making it hard to understand the component tree without reading all 3,400 lines.

**-1: Global mutable state without explicit documentation.** `_EVER_CONNECTED` and `_APP_START` are module-level mutable variables in `dashboard.py`. `CLAUDE.md` documents the GIL-safety reasoning for `_demo_mode` and `_last_intel_tickers` in `ibkr_client.py`, but does not cover the dashboard-level globals. This is a minor gap but worth noting.

---

## 3. Code Quality & Craft — 18/25

### What Works Well

**Inline comments are excellent.** Nearly every non-obvious decision is explained at the point of use. The `ibkr_client.py` module header alone documents the design philosophy, port fallback strategy, threading model, and heartbeat rationale in 20 lines of clear prose. This is well above average for a personal project.

**Error handling is thorough.** `ibkr_client.py` has 18 `try/except` blocks; `market_intel.py` and `market_valuation.py` each have 13. Every network call degrades gracefully — returning `None`, an empty dict, or a fallback value rather than propagating exceptions to the UI. The `safe()` helper in `_do_fetch()` handles NaN values from the IB API cleanly.

**The `from __future__ import annotations` import** is present in all 15 Python modules, enabling deferred evaluation of type hints — a good modern Python practice.

**Partial type annotations** are present: 74 of 175 functions (42%) carry at least one annotation. The public API of `ibkr_client.py` is fully annotated; `coach.py` and `data_processor.py` are partially annotated.

### Deductions

**~~-4: No automated tests whatsoever.~~** ✅ Fixed — smoke_test.py + GitHub Actions CI.

**[original]** `CLAUDE.md` explicitly states: "There are no tests or linting commands configured. The app is validated by running it." For a financial application — even a read-only one — this is a significant gap. `data_processor.py`, `coach.py`, `analytics.py`, and `cache_util.py` are all pure or near-pure functions that could be unit-tested without any IBKR connection. The absence of tests means regressions are caught only by manual inspection.

**~~-2: No linting or formatting configuration.~~** ✅ Fixed — pyproject.toml (ruff) + CI.

**[original]** There is no `pyproject.toml`, `setup.cfg`, `.flake8`, `mypy.ini`, or pre-commit hook. Code style is consistent within files but not enforced. A `ruff` or `flake8` configuration and a `mypy` strict pass would catch real bugs (e.g., the `v == v` NaN check in `ibkr_client.py` line 275, which works but is non-idiomatic — `math.isnan()` or `pd.isna()` is clearer).

**-1: Incomplete type annotation coverage.** 58% of functions lack annotations. `dashboard.py` callbacks in particular have no return type hints, making it harder for static analysis tools to catch `no_update` vs. actual return type mismatches.

**-1: Magic numbers scattered across `dashboard.py`.** Despite `styles.py` existing to centralize visual constants, `dashboard.py` still contains dozens of inline hex codes (`'#888'`, `'#111'`, `'#f5f5f5'`), font sizes (`'13px'`, `'14px'`, `'15px'`), and border radii (`'8px'`, `'14px'`) that are not referenced from `styles.py`. The system is partially applied.

---

## 4. Organization & Documentation — 14/15

### What Works Well

The **module decomposition** (outside of `dashboard.py`) is clean and logical. Each file has a single, well-defined responsibility: `ibkr_client.py` owns the IB connection, `data_processor.py` owns pandas transforms, `market_intel.py` owns yfinance enrichment, `market_valuation.py` owns macro indicators, `coach.py` owns rules-based scenarios, `ai_provider.py` owns LLM integration, and `cache_util.py` owns caching infrastructure.

**`CLAUDE.md`** is an outstanding piece of developer documentation for a project of this size. It covers the runtime model, data flow diagram, threading model, cross-thread rules, shared-state assumptions, module responsibilities, Dash patterns, caching layers, configuration priority, CI/release behavior, and a step-by-step guide for adding new dashboard sections. This level of documentation is rare in hobby projects and reflects genuine care for maintainability.

**`README.md`** is thorough and user-focused, with clear installation paths for three audiences (Docker one-liner, release zip, developer source), a configuration reference table, troubleshooting section, and tech stack summary.

**`SETUP.txt`** provides a plain-English, non-technical setup guide bundled in the release zip — a thoughtful touch for non-developer users.

### Deductions

**-1: README/CLAUDE.md claim 8 coach scenarios; only 5 exist.** As noted above, this is a concrete documentation inaccuracy. The `CLAUDE.md` entry for `coach.py` says "8 pure scenario functions" and lists "earnings watch, currency exposure, winners & losers" as scenarios, but these are not implemented in `coach.py`.

---

## 5. DevOps & Distribution — 9/10

### What Works Well

The CI/CD pipeline is well-constructed for a solo project. Two GitHub Actions workflows handle distinct concerns: `docker-publish.yml` builds and pushes a multi-platform image (amd64 + arm64) on every push to `main`, and `release.yml` builds a versioned image and creates a GitHub Release with a user-facing setup zip on every version tag. Both use `actions/checkout@v4`, `docker/build-push-action@v5`, and GitHub Actions cache for layer reuse — all current best practices.

The **Dockerfile** follows the two-stage pattern correctly: a builder stage installs compiled dependencies into `/install`, and a slim runtime stage copies only the installed packages and application source, runs as a non-root user, and includes an HTTP healthcheck. This is production-grade container hygiene.

The **`docker-compose.yml`** is clean, documents the Linux `extra_hosts` workaround, and uses `restart: unless-stopped` for resilience.

### Deductions

**-1: No pinned dependency versions in `requirements.txt`.** All nine dependencies are unpinned (`ib_async`, `pandas`, `numpy`, `plotly`, `dash`, `reportlab`, `yfinance`, `pyyaml`, `diskcache`). This means the Docker image built today may differ from one built in three months, and a breaking change in any dependency (particularly `yfinance`, which has a history of API changes) will silently break the build. Adding a `requirements.lock` or pinning to known-good versions (e.g., `dash>=2.18,<3`) would significantly improve reproducibility.

---

## Recommendations by Priority

### Priority 1 — High Impact, Low Effort

**1.1 Fix the scenario count discrepancy.** Either implement the three missing coach scenarios (earnings watch, currency exposure, winners & losers) or update the README and `CLAUDE.md` to accurately reflect the five that exist. This is a one-line fix for the documentation path.

**1.2 Pin dependency versions.** Run `pip freeze > requirements.lock` and reference it in the Dockerfile (`COPY requirements.lock . && pip install -r requirements.lock`). At minimum, pin the most volatile dependencies: `yfinance`, `ib_async`, and `dash`.

**1.3 Add a minimal test suite.** Start with `pytest` and target the pure-function modules. `data_processor.process_positions()`, `coach.scenario_performance()`, `cache_util.cached_fetch()`, and `trade_history.parse_activity_csv()` can all be tested with synthetic data and no IBKR connection. Even 20–30 tests would catch the most common regressions.

**1.4 Add `ruff` or `flake8` to CI.** A single step in `docker-publish.yml` running `ruff check .` or `flake8 --max-line-length=120 .` would enforce consistent style and catch common errors automatically.

### Priority 2 — Medium Impact, Medium Effort

**2.1 Split `dashboard.py` into a package.** The planned `dashboard_core/` split should be executed. A natural decomposition would be:

| New file | Contents |
|---|---|
| `dashboard_core/layout.py` | `app` instance, `app.layout`, helper functions |
| `dashboard_core/callbacks_portfolio.py` | Holdings, summary cards, donut, position detail |
| `dashboard_core/callbacks_intel.py` | Market intelligence, earnings, dividends |
| `dashboard_core/callbacks_valuation.py` | Buffett, CAPE, P/E, Treasury yield |
| `dashboard_core/callbacks_coach.py` | Coach panel, LLM integration |
| `dashboard_core/callbacks_misc.py` | PDF export, toast, keyboard shortcuts, retry/demo |

This reduces the largest file from 3,417 lines to six files averaging ~570 lines each — a manageable size for any single concern.

**2.2 Consolidate the EUR/USD fallback.** Replace the six scattered `1.08` literals with a single reference to `cfg['display']['eurusd_fallback']`. Add a note to the README that the dashboard is EUR-primary and document how to change the fallback for non-European users.

**2.3 Complete the `styles.py` migration.** Audit `dashboard.py` for inline hex codes and font sizes that duplicate values already defined in `styles.py`. Replacing them with named constants completes the intent of the style system and makes theme changes trivial.

**2.4 Add `mypy` with partial strictness.** Running `mypy --ignore-missing-imports --no-strict-optional *.py` on the non-dashboard modules would surface real type errors at zero cost to the user experience. Annotating the remaining 58% of functions is a longer-term goal.

### Priority 3 — Lower Impact or Higher Effort

**3.1 Responsive layout.** Replace fixed-width card grids with CSS Grid `auto-fit` / `minmax` patterns or integrate [Dash Bootstrap Components](https://dash-bootstrap-components.opensource.faculty.ai/) for a responsive grid system. This would make the dashboard usable on tablets and phones.

**3.2 Implement the three missing coach scenarios.** The earnings watch, currency exposure, and winners & losers scenarios are already described in the documentation and would complete the rules-based coach. All three can be implemented as pure functions using data already present in the `portfolio-data`, `market-intel-data`, and `valuation-data` stores.

**3.3 Add a `CHANGELOG.md`.** The commit history is informative but not structured. A changelog following [Keep a Changelog](https://keepachangelog.com/) conventions would make it easier for users to understand what changed between versions.

**3.4 Consider WebSocket-based live updates.** The current 60-second polling interval is a reasonable default, but Dash supports `dcc.Interval` at sub-second rates and `dash-extensions` provides WebSocket support. For users who want near-real-time P&L updates, a configurable push model would be a meaningful upgrade.

**3.5 Multi-account support.** The current architecture assumes a single IBKR account. Adding support for multiple `client_id` connections (e.g., paper + live side by side) would require changes to `ibkr_client.py` and `dashboard.py` but would significantly expand the tool's utility.

---

## Summary Table

| Attribute | Current State | Recommended Action |
|---|---|---|
| Feature breadth | Excellent — live data, macro, coach, demo mode | Implement 3 missing coach scenarios |
| Connection resilience | Excellent — exponential back-off, heartbeat, port fallback | No change needed |
| `dashboard.py` size | Critical concern — 3,417 lines, 40 callbacks | Split into `dashboard_core/` package |
| Test coverage | None | Add pytest suite for pure-function modules |
| Dependency pinning | Unpinned | Pin versions in `requirements.lock` |
| Documentation accuracy | Minor drift (scenario count) | Fix README/CLAUDE.md scenario count |
| Type annotations | 42% of functions | Extend coverage; add mypy to CI |
| Style consistency | Partially applied | Complete `styles.py` migration |
| Mobile responsiveness | None | Add CSS media queries or Dash Bootstrap |
| CI/CD | Good — multi-platform Docker, release zip | Add linting step |
| EUR-centric assumptions | Undocumented | Document and consolidate fallback |

---

*Review conducted by static analysis of the full repository source, commit history, and documentation. No live IBKR connection was used.*
