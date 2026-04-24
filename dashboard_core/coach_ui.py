"""AI Coach panel — chat with optional LLM backend.

Single unified panel shown below Holdings when the ✨ Ask button is clicked.
Top section: 5 rules-based scenarios from coach.py — pure Python, no network.
Bottom section: optional API key unlocks 6 deeper preset questions + a
free-form "ask anything" input. Key stored in browser localStorage only.

This module owns the largest concentrated UI surface in the dashboard
(~18 callbacks + 4 clientside callbacks + a dozen render helpers).
Everything is wrapped inside register(app) so it can share `app` as a
closure with the helpers without re-introducing module-level state.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime

import requests
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

import ai_provider
from coach import SCENARIOS, render_scenario
from styles import (
    CARD,
    COLOR_BORDER_MID,
    COLOR_GOOD,
    COLOR_GOOD_BG,
    COLOR_SURFACE,
    COLOR_SURFACE_SOFT,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    COLOR_TEXT_MID,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_SLATE,
    COLOR_TEXT_STRONG,
    COLOR_WARN,
    COLOR_WARN_BG,
)

log = logging.getLogger(__name__)


def register(app):
    _COACH_BTN = {
        'fontSize': '13px', 'color': COLOR_TEXT_MID, 'background': COLOR_SURFACE,
        'border': '0.5px solid #ddd', 'borderRadius': '8px',
        'padding': '6px 14px', 'cursor': 'pointer',
        'transition': 'background 120ms ease, color 120ms ease',
    }

    _COACH_BTN_PRIMARY = {
        **_COACH_BTN,
        'background': COLOR_TEXT_STRONG, 'color': COLOR_SURFACE_WHITE, 'border': '0.5px solid #111',
    }

    _COACH_INPUT = {
        'width': '100%', 'padding': '8px 10px', 'fontSize': '14px',
        'border': '0.5px solid #ddd', 'borderRadius': '8px', 'marginBottom': '8px',
    }

    _COACH_SECTION_LABEL = {
        'fontSize': '12px', 'color': COLOR_TEXT_MUTED, 'margin': '0 0 10px',
        'textTransform': 'uppercase', 'letterSpacing': '0.08em', 'fontWeight': '600',
    }


    # ── Thread helpers ────────────────────────────────────────────────────────────
    # A "thread" is one named conversation:
    #   {id: str, title: str, created: iso, history: [{q, a, error?, followups?}]}
    # Threads are persisted in browser localStorage via the coach-threads store.

    def _new_thread(history: list | None = None) -> dict:
        h = list(history or [])
        return {
            'id':      uuid.uuid4().hex[:12],
            'title':   _thread_title(h),
            'created': datetime.utcnow().isoformat() + 'Z',
            'history': h,
        }


    def _thread_title(history: list) -> str:
        if not history:
            return 'New chat'
        first_q = (history[0].get('q') or '').strip().replace('\n', ' ')
        if not first_q:
            return 'New chat'
        return first_q[:40] + ('…' if len(first_q) > 40 else '')


    def _find_thread(threads: list, thread_id: str | None) -> dict | None:
        if not thread_id:
            return None
        for t in threads or []:
            if t.get('id') == thread_id:
                return t
        return None


    def _active_history(threads: list, active_id: str | None) -> list:
        t = _find_thread(threads, active_id)
        return list(t.get('history') or []) if t else []


    def _commit_history(threads: list, active_id: str | None,
                        history: list) -> tuple[list, str]:
        """Write `history` into the active thread, creating one if needed.
        Returns (threads, active_id).  Auto-titles from the first user message."""
        threads = list(threads or [])
        t = _find_thread(threads, active_id)
        if t is None:
            t = _new_thread(history)
            threads.insert(0, t)
            active_id = t['id']
        else:
            t['history'] = list(history)
            # Only retitle if the title is still the default placeholder.
            if t.get('title', 'New chat') in ('New chat', ''):
                t['title'] = _thread_title(history)
        return threads, active_id


    _USER_BUBBLE = {
        'maxWidth': '80%', 'padding': '10px 14px', 'borderRadius': '16px 16px 4px 16px',
        'background': COLOR_TEXT_STRONG, 'color': COLOR_SURFACE_WHITE, 'fontSize': '14px', 'lineHeight': '1.5',
        'whiteSpace': 'pre-wrap', 'wordBreak': 'break-word',
    }
    _ASSIST_BUBBLE = {
        'maxWidth': '88%', 'padding': '12px 16px', 'borderRadius': '16px 16px 16px 4px',
        'background': '#f4f4f5', 'color': COLOR_TEXT_STRONG, 'fontSize': '14px', 'lineHeight': '1.55',
        'wordBreak': 'break-word',
    }
    _ASSIST_ERR = {**_ASSIST_BUBBLE, 'background': COLOR_WARN_BG,
                   'border': '0.5px solid #fde68a', 'color': COLOR_WARN}

    _CHIP_STYLE = {
        'fontSize': '12px', 'padding': '6px 12px', 'cursor': 'pointer',
        'background': COLOR_SURFACE_WHITE, 'border': '0.5px solid #e5e7eb', 'borderRadius': '999px',
        'color': COLOR_TEXT_SLATE, 'transition': 'all 120ms ease', 'textAlign': 'left',
        'lineHeight': '1.3',
    }

    _ICON_BTN = {
        'background': 'transparent', 'border': 'none', 'cursor': 'pointer',
        'color': COLOR_TEXT_MUTED, 'fontSize': '12px', 'padding': '2px 6px',
        'borderRadius': '6px', 'marginLeft': '6px',
    }

    _STARTER_PROMPTS = [
        "Give me a full portfolio health check in 5 bullet points.",
        "What would a conservative investor change here?",
        "Where would you put €500 more today?",
        "Three realistic risks in the next 12 months?",
    ]


    def _user_row(text: str, is_last: bool = False):
        edit_btn = None
        if is_last:
            edit_btn = html.Button(
                "✎ Edit", id='coach-edit-btn', n_clicks=0, title="Edit this question",
                className='coach-icon-btn',
                style={**_ICON_BTN, 'marginTop': '4px'})
        return html.Div([
            html.Div(text, style=_USER_BUBBLE),
            edit_btn,
        ], style={'display': 'flex', 'flexDirection': 'column',
                  'alignItems': 'flex-end', 'marginBottom': '10px'})


    def _assistant_row(text: str, idx: int, err: bool = False, is_last: bool = False,
                       followups: list[str] | None = None):
        body = (
            html.Div(text, style={**_ASSIST_ERR, 'whiteSpace': 'pre-wrap'})
            if err else
            html.Div(dcc.Markdown(text, style={'margin': '0'}), style=_ASSIST_BUBBLE)
        )
        actions = None
        if not err:
            action_btns = [
                html.Button("Copy", id={'type': 'coach-copy', 'index': idx},
                            n_clicks=0, title="Copy answer",
                            className='coach-icon-btn', style=_ICON_BTN),
            ]
            if is_last:
                action_btns.append(html.Button(
                    "↻ Regenerate", id='coach-regenerate-btn', n_clicks=0,
                    title="Retry this question", className='coach-icon-btn',
                    style=_ICON_BTN))
            actions = html.Div(action_btns, style={
                'display': 'flex', 'marginTop': '4px',
                'marginLeft': '6px', 'opacity': '0.7'})

        chips = None
        if is_last and followups:
            chips = html.Div([
                html.Div("Suggested follow-ups", style={
                    'fontSize': '11px', 'color': COLOR_TEXT_MUTED, 'margin': '10px 4px 6px',
                    'letterSpacing': '0.04em', 'textTransform': 'uppercase',
                    'fontWeight': '600',
                }),
                html.Div([
                    html.Button(f, id={'type': 'coach-followup', 'index': i},
                                n_clicks=0, className='coach-chip', style=_CHIP_STYLE)
                    for i, f in enumerate(followups)
                ], style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '6px',
                          'marginLeft': '2px'}),
            ])

        return html.Div([
            html.Div([body, actions] if actions else [body],
                     style={'display': 'flex', 'flexDirection': 'column',
                            'alignItems': 'flex-start'}),
            chips,
        ], style={'display': 'flex', 'flexDirection': 'column',
                  'alignItems': 'flex-start', 'marginBottom': '14px'})


    def _thinking_bubble():
        return html.Div(
            html.Div([
                html.Span(".", style={'animation': 'coachPulse 1.2s infinite', 'animationDelay': '0s'}),
                html.Span(".", style={'animation': 'coachPulse 1.2s infinite', 'animationDelay': '0.2s', 'marginLeft': '2px'}),
                html.Span(".", style={'animation': 'coachPulse 1.2s infinite', 'animationDelay': '0.4s', 'marginLeft': '2px'}),
                html.Span("Thinking…", style={'marginLeft': '8px', 'color': COLOR_TEXT_MUTED,
                                               'fontSize': '13px'}),
            ], style={**_ASSIST_BUBBLE, 'display': 'flex', 'alignItems': 'center',
                      'color': COLOR_TEXT_DIM}),
            style={'display': 'flex', 'justifyContent': 'flex-start', 'marginBottom': '10px'},
        )


    def _starter_panel():
        return html.Div([
            html.Div([
                html.Button(p, id={'type': 'coach-starter', 'index': i},
                            n_clicks=0, className='coach-chip', style=_CHIP_STYLE)
                for i, p in enumerate(_STARTER_PROMPTS)
            ], style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '6px',
                      'justifyContent': 'center'}),
        ])


    def _chat_bubbles(history: list[dict], pending: str | None = None):
        """Render the chat log — ChatGPT-style split bubbles."""
        if not history and not pending:
            return _starter_panel()

        rows: list = []
        last_idx = len(history) - 1
        for i, turn in enumerate(history):
            q = turn.get('q', '')
            a = turn.get('a', '')
            err = bool(turn.get('error'))
            fups = turn.get('followups') or []
            # "Edit" shows on the last user turn only when no request is in flight
            # and the last turn has a completed answer.
            rows.append(_user_row(q, is_last=(i == last_idx and not pending)))
            rows.append(_assistant_row(a, idx=i, err=err,
                                       is_last=(i == last_idx and not pending),
                                       followups=fups))
        if pending:
            rows.append(_user_row(pending))
            rows.append(_thinking_bubble())

        return html.Div(rows)


    @app.callback(
        Output('coach-open', 'data'),
        Input('coach-toggle-btn', 'n_clicks'),
        State('coach-open', 'data'),
        prevent_initial_call=True,
    )
    def toggle_coach(n, is_open):
        if not n:
            return no_update
        return not bool(is_open)


    @app.callback(
        Output('coach-open', 'data', allow_duplicate=True),
        Input('coach-close-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def close_coach(n):
        if not n:
            return no_update
        return False


    @app.callback(
        Output('coach-active-id', 'data'),
        Input({'type': 'coach-preset-btn', 'index': ALL}, 'n_clicks'),
        prevent_initial_call=True,
    )
    def pick_scenario(clicks):
        trig = ctx.triggered_id
        if not isinstance(trig, dict):
            return no_update
        # Guard against pattern-matching phantom triggers on mount.
        if not any(clicks or []):
            return no_update
        return trig.get('index') or no_update


    @app.callback(
        Output('coach-mode', 'data'),
        Input('coach-mode-preset-btn', 'n_clicks'),
        Input('coach-mode-ai-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def switch_mode(p, a):
        trig = ctx.triggered_id
        if trig == 'coach-mode-ai-btn' and a:
            return 'ai'
        if trig == 'coach-mode-preset-btn' and p:
            return 'preset'
        return no_update


    @app.callback(
        Output('coach-api-key', 'data'),
        Input('coach-save-key-btn',  'n_clicks'),
        Input('coach-clear-key-btn', 'n_clicks'),
        State('coach-key-input', 'value'),
        prevent_initial_call=True,
    )
    def save_or_clear_key(save, clear, value):
        trig = ctx.triggered_id
        if trig == 'coach-clear-key-btn' and clear:
            return ''
        if trig == 'coach-save-key-btn' and save:
            return (value or '').strip()
        return no_update


    # ── Chat: submit → pending-q, then run_llm consumes pending-q ────────────────

    @app.callback(
        Output('coach-pending-q', 'data'),
        Output('coach-input', 'value'),
        Output('coach-input', 'disabled'),
        Output('coach-send-btn', 'disabled'),
        Output('coach-send-btn', 'children'),
        Input('coach-send-btn', 'n_clicks'),
        Input('coach-input', 'n_submit'),
        Input({'type': 'coach-starter',  'index': ALL}, 'n_clicks'),
        Input({'type': 'coach-followup', 'index': ALL}, 'n_clicks'),
        State('coach-input', 'value'),
        State({'type': 'coach-starter',  'index': ALL}, 'children'),
        State({'type': 'coach-followup', 'index': ALL}, 'children'),
        State('coach-pending-q', 'data'),
        prevent_initial_call=True,
    )
    def submit_question(send_n, submit_n, starter_clicks, fup_clicks,
                        text, starter_labels, fup_labels, pending):
        noop = (no_update,) * 5

        # If a request is already in flight, ignore new submissions.
        if pending:
            return noop

        trig = ctx.triggered_id
        q = None
        if trig == 'coach-send-btn' or trig == 'coach-input':
            q = (text or '').strip()
        elif isinstance(trig, dict):
            t = trig.get('type')
            i = trig.get('index')
            # Guard against the phantom trigger that pattern-matching Inputs
            # sometimes fire on mount (n_clicks is None/0 in that case).
            clicks = starter_clicks if t == 'coach-starter' else fup_clicks
            labels = starter_labels if t == 'coach-starter' else fup_labels
            if i is None or i >= len(clicks) or not clicks[i]:
                return noop
            q = (labels[i] if i < len(labels) else '').strip()

        if not q:
            return noop

        # Set pending-q → triggers run_llm via its Input. Disable input/button
        # while the request is in flight; run_llm re-enables them on completion.
        return q, '', True, True, "Sending…"


    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Output('coach-pending-q', 'data', allow_duplicate=True),
        Output('coach-input', 'disabled', allow_duplicate=True),
        Output('coach-send-btn', 'disabled', allow_duplicate=True),
        Output('coach-send-btn', 'children', allow_duplicate=True),
        Input('coach-pending-q', 'data'),
        State('coach-api-key', 'data'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        State('portfolio-data', 'data'),
        State('market-intel-data', 'data'),
        State('valuation-data', 'data'),
        prevent_initial_call=True,
    )
    def run_llm(question, key, threads, active_id, port, intel, val):
        # Triggered when submit_question writes a question into pending-q.
        # Also fires when we clear pending-q at the end — we bail on that.
        log.info("coach.run_llm fired: question=%r key_present=%s",
                 (question or '')[:60], bool(key))
        if not question:
            return no_update, no_update, no_update, no_update, no_update, no_update

        try:
            history = _active_history(threads, active_id)
            for turn in history:
                turn.pop('followups', None)

            def _commit_and_return(h):
                new_threads, new_active = _commit_history(threads, active_id, h)
                return new_threads, new_active, None, False, False, "Send ↑"

            if not key:
                history.append({'q': question,
                                'a': "No API key saved. Paste one to enable chat.",
                                'error': True})
                return _commit_and_return(history)

            try:
                provider = ai_provider.detect_provider(key)
                log.info("coach: calling provider=%s", provider)
                context_json = ai_provider.build_portfolio_context(port, intel, val)
                t0 = time.time()
                answer, followups = ai_provider.ask(key, context_json, question,
                                                    history=history)
                log.info("coach: provider reply in %.1fs, %d chars",
                         time.time() - t0, len(answer or ''))
                if not (answer or '').strip():
                    history.append({'q': question,
                                    'a': "The provider returned an empty response. "
                                         "Try again or switch to a different model.",
                                    'error': True})
                else:
                    history.append({'q': question, 'a': answer, 'followups': followups})
            except requests.HTTPError as e:
                body = ''
                try:
                    body = e.response.text[:300]
                except Exception:
                    pass
                status = e.response.status_code if e.response else '?'
                log.warning("coach: HTTPError %s: %s", status, body)
                history.append({
                    'q': question,
                    'a': f"Provider returned an error ({status}). "
                         f"Check that the key is valid and has credit.\n{body}",
                    'error': True,
                })
            except Exception as e:
                log.exception("coach: provider call failed")
                history.append({'q': question,
                                'a': f"Couldn't reach the provider: {type(e).__name__}: {e}",
                                'error': True})
            return _commit_and_return(history)
        except Exception:
            log.exception("coach.run_llm crashed")
            # Return a safe state so the UI unsticks from "Thinking…"
            return no_update, no_update, None, False, False, "Send ↑"


    # ── Derived: chat-history = active thread's history ──────────────────────────

    @app.callback(
        Output('coach-chat-history', 'data'),
        Input('coach-threads', 'data'),
        Input('coach-active-thread-id', 'data'),
    )
    def _derive_chat_history(threads, active_id):
        return _active_history(threads, active_id)


    # ── Render the thread-tabs row. Kept separate from render_coach so sending a
    #    message doesn't rebuild the whole panel.

    @app.callback(
        Output('coach-tabs-row', 'children'),
        Input('coach-threads', 'data'),
        Input('coach-active-thread-id', 'data'),
    )
    def render_thread_tabs(threads, active_thread_id):
        threads = threads or []
        tabs: list = []
        for t in threads:
            is_active = (t.get('id') == active_thread_id)
            tab_label = t.get('title') or 'New chat'
            tabs.append(html.Div([
                html.Button(
                    tab_label,
                    id={'type': 'coach-thread-tab', 'index': t['id']},
                    n_clicks=0,
                    title=tab_label,
                    style={
                        'background': COLOR_TEXT_STRONG if is_active else COLOR_SURFACE_WHITE,
                        'color':      COLOR_SURFACE_WHITE if is_active else COLOR_TEXT_SLATE,
                        'border':     '0.5px solid ' + (COLOR_TEXT_STRONG if is_active else COLOR_BORDER_MID),
                        'borderRadius': '999px 0 0 999px',
                        'padding': '4px 10px', 'fontSize': '12px',
                        'cursor': 'pointer', 'maxWidth': '180px',
                        'overflow': 'hidden', 'textOverflow': 'ellipsis',
                        'whiteSpace': 'nowrap', 'fontWeight': '500',
                    }),
                html.Button(
                    '×',
                    id={'type': 'coach-thread-del', 'index': t['id']},
                    n_clicks=0, title='Delete chat',
                    style={
                        'background': COLOR_TEXT_STRONG if is_active else COLOR_SURFACE_WHITE,
                        'color':      COLOR_SURFACE_WHITE if is_active else '#9ca3af',
                        'border':     '0.5px solid ' + (COLOR_TEXT_STRONG if is_active else COLOR_BORDER_MID),
                        'borderLeft': 'none',
                        'borderRadius': '0 999px 999px 0',
                        'padding': '4px 8px', 'fontSize': '12px',
                        'cursor': 'pointer', 'fontWeight': '600',
                    }),
            ], style={'display': 'flex', 'alignItems': 'center', 'marginRight': '6px'}))
        return tabs


    # ── Thread management: clear / new / switch / delete ─────────────────────────

    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Input('coach-clear-chat-btn', 'n_clicks'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        prevent_initial_call=True,
    )
    def clear_chat(n, threads, active_id):
        """Clear the current thread's history (keep the thread; fresh canvas)."""
        if not n:
            return no_update, no_update
        threads = list(threads or [])
        t = _find_thread(threads, active_id)
        if t is None:
            return threads, active_id
        t['history'] = []
        t['title']   = 'New chat'
        return threads, active_id


    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Input('coach-new-thread-btn', 'n_clicks'),
        State('coach-threads', 'data'),
        prevent_initial_call=True,
    )
    def new_thread(n, threads):
        if not n:
            return no_update, no_update
        threads = list(threads or [])
        # If the current first thread is already empty, just reuse it.
        if threads and not (threads[0].get('history') or []):
            return threads, threads[0]['id']
        t = _new_thread([])
        threads.insert(0, t)
        return threads, t['id']


    @app.callback(
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Input({'type': 'coach-thread-tab', 'index': ALL}, 'n_clicks'),
        prevent_initial_call=True,
    )
    def switch_thread(clicks):
        trig = ctx.triggered_id
        if not isinstance(trig, dict):
            return no_update
        if not any(clicks or []):
            return no_update
        return trig.get('index') or no_update


    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Input({'type': 'coach-thread-del', 'index': ALL}, 'n_clicks'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        prevent_initial_call=True,
    )
    def delete_thread(clicks, threads, active_id):
        trig = ctx.triggered_id
        if not isinstance(trig, dict) or not any(clicks or []):
            return no_update, no_update
        tid = trig.get('index')
        threads = [t for t in (threads or []) if t.get('id') != tid]
        if active_id == tid:
            active_id = threads[0]['id'] if threads else None
        return threads, active_id


    # ── Regenerate last answer: pop last turn and resubmit its question ──────────

    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Output('coach-pending-q', 'data', allow_duplicate=True),
        Output('coach-input', 'disabled', allow_duplicate=True),
        Output('coach-send-btn', 'disabled', allow_duplicate=True),
        Output('coach-send-btn', 'children', allow_duplicate=True),
        Input('coach-regenerate-btn', 'n_clicks'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        State('coach-pending-q', 'data'),
        prevent_initial_call=True,
    )
    def regenerate_last(n, threads, active_id, pending):
        if not n or pending:
            return (no_update,) * 6
        history = _active_history(threads, active_id)
        if not history:
            return (no_update,) * 6
        last_q = (history[-1].get('q') or '').strip()
        if not last_q:
            return (no_update,) * 6
        history = history[:-1]
        new_threads, new_active = _commit_history(threads, active_id, history)
        return new_threads, new_active, last_q, True, True, "Sending…"


    # ── Edit last question: pop last turn and pre-fill the input ─────────────────

    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Output('coach-prefill', 'data', allow_duplicate=True),
        Input('coach-edit-btn', 'n_clicks'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        prevent_initial_call=True,
    )
    def edit_last(n, threads, active_id):
        if not n:
            return no_update, no_update, no_update
        history = _active_history(threads, active_id)
        if not history:
            return no_update, no_update, no_update
        last_q = (history[-1].get('q') or '')
        history = history[:-1]
        new_threads, new_active = _commit_history(threads, active_id, history)
        return new_threads, new_active, last_q


    # ── Chat output: separate callback so typing/scrolling doesn't rebuild panel ─

    @app.callback(
        Output('coach-chat-output', 'children'),
        Input('coach-chat-history', 'data'),
        Input('coach-pending-q', 'data'),
    )
    def render_chat(history, pending):
        return _chat_bubbles(history or [], pending)


    # ── Clientside: copy an answer to clipboard ─────────────────────────────────

    app.clientside_callback(
        """
        function(clicks, history) {
            const ctx = window.dash_clientside.callback_context;
            if (!ctx.triggered || !ctx.triggered.length) return window.dash_clientside.no_update;
            const trig = ctx.triggered[0];
            if (!trig.value) return window.dash_clientside.no_update;
            try {
                const id = JSON.parse(trig.prop_id.split('.')[0]);
                const turn = (history || [])[id.index] || {};
                const txt = turn.a || '';
                if (navigator.clipboard && txt) { navigator.clipboard.writeText(txt); }
            } catch (e) {}
            return (Date.now());
        }
        """,
        Output('coach-copy-signal', 'data'),
        Input({'type': 'coach-copy', 'index': ALL}, 'n_clicks'),
        State('coach-chat-history', 'data'),
        prevent_initial_call=True,
    )


    # ── Clientside: auto-scroll the chat area to the bottom on new content ──────

    app.clientside_callback(
        """
        function(_children) {
            setTimeout(function() {
                var el = document.getElementById('coach-chat-output');
                if (el) { el.scrollTop = el.scrollHeight; }
            }, 30);
            return Date.now();
        }
        """,
        Output('coach-scroll-signal', 'data'),
        Input('coach-chat-output', 'children'),
        prevent_initial_call=True,
    )


    # ── Clientside: smooth-scroll to a panel when it opens ──────────────────────
    # Target-Y math: let the BROWSER do it. We call scrollIntoView({block:'start'})
    # synchronously, read the resulting pageYOffset (which the browser has placed
    # exactly where it should land, respecting scroll-margin-top from custom.css),
    # then immediately revert to the original scroll position. Both writes happen
    # in the same JS task so the browser only paints the final state — the jump
    # is invisible. This eliminates any manual math (offsetTop chains, transform
    # interference, flex/grid edge cases) and produces always-correct target Y.
    #
    # Speed: browser-native scrollIntoView({behavior:'smooth'}) is fixed and fast.
    # We replace it with a requestAnimationFrame tween using easeInOutCubic over
    # ~800 ms for a more deliberate feel. Reduced-motion users get an instant
    # jump via the matchMedia check.

    _SMOOTH_SCROLL_JS_TMPL = """
    function(%(trigger)s) {
        %(guard)s
        setTimeout(function() {
            var el = document.getElementById('%(element_id)s');
            if (!el) { return; }

            // Capture the browser's own idea of the correct scroll target by
            // jumping there synchronously, reading pageYOffset, then reverting.
            // Both scroll writes happen in one JS task — nothing is painted
            // between them, so the jump-and-revert is invisible to the user.
            var startY = window.pageYOffset;
            el.scrollIntoView({ block: 'start' });
            var targetY = window.pageYOffset;
            window.scrollTo(0, startY);

            var dy = targetY - startY;
            if (Math.abs(dy) < 2) { return; }

            // Respect OS "reduce motion" preference — skip the animation.
            var reduce = window.matchMedia
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (reduce) { window.scrollTo(0, targetY); return; }

            var duration = 800;
            var start = performance.now();
            function step(now) {
                var t = (now - start) / duration;
                if (t > 1) { t = 1; }
                // easeInOutCubic
                var ease = t < 0.5
                    ? 4 * t * t * t
                    : 1 - Math.pow(-2 * t + 2, 3) / 2;
                window.scrollTo(0, startY + dy * ease);
                if (t < 1) { requestAnimationFrame(step); }
            }
            requestAnimationFrame(step);
        }, %(delay)d);
        return Date.now();
    }
    """

    # Position-detail: trigger on the rendered children (DOM element exists),
    # skip when children is null (panel closed).
    app.clientside_callback(
        _SMOOTH_SCROLL_JS_TMPL % {
            'trigger':    'children',
            'guard':      'if (!children) { return window.dash_clientside.no_update; }',
            'element_id': 'position-detail',
            'delay':      280,   # wait for slideInDown (250 ms) + a safety margin
        },
        Output('position-detail-scroll-signal', 'data'),
        Input('position-detail', 'children'),
        prevent_initial_call=True,
    )

    # Coach panel: trigger on coach-open flipping to True. NOT on coach-panel.children
    # (that would yank the page up on every chat keystroke during a re-render).
    app.clientside_callback(
        _SMOOTH_SCROLL_JS_TMPL % {
            'trigger':    'isOpen',
            'guard':      'if (!isOpen) { return window.dash_clientside.no_update; }',
            'element_id': 'coach-panel',
            'delay':      80,    # coach render is server-side only; no slide animation
        },
        Output('coach-panel-scroll-signal', 'data'),
        Input('coach-open', 'data'),
        prevent_initial_call=True,
    )


    # ── Prefill: cleared on the next tick after it's consumed by render_coach ───
    # coach-input reads prefill as its initial value. We clear the store right
    # after so the next panel render doesn't re-insert the same text.

    @app.callback(
        Output('coach-prefill', 'data', allow_duplicate=True),
        Input('coach-panel', 'children'),
        State('coach-prefill', 'data'),
        prevent_initial_call=True,
    )
    def clear_prefill_after_render(_children, prefill):
        return '' if prefill else no_update


    # ── Ask coach about a specific ticker ────────────────────────────────────────
    # Button lives inside the position-detail panel. Clicking it opens the coach,
    # switches to AI mode, and pre-fills the input with a specific, actionable
    # question about the ticker so the user can send immediately or lightly edit.

    _ASK_COACH_TEMPLATE = (
        "Analyse my {ticker} position: is my position size appropriate given my "
        "overall portfolio, what are the main risks to watch, and does anything "
        "about the current valuation stand out?"
    )

    # Alternative templates — swap into _ASK_COACH_TEMPLATE if preferred:
    #   "What should I know about {ticker} right now? Cover the thesis, any recent"
    #   " news worth flagging, and how it fits with my other holdings."
    #
    #   "Give me a quick {ticker} health check: recent performance, fundamentals,"
    #   " and whether I should consider trimming, holding, or adding."
    #
    #   "Explain {ticker} to me like I'm new to investing — what does the company"
    #   " do, why might someone hold it, and what are the biggest risks?"


    @app.callback(
        Output('coach-open',    'data', allow_duplicate=True),
        Output('coach-mode',    'data', allow_duplicate=True),
        Output('coach-prefill', 'data', allow_duplicate=True),
        Input({'type': 'position-ask-coach', 'index': ALL}, 'n_clicks'),
        State('selected-ticker', 'data'),
        prevent_initial_call=True,
    )
    def ask_coach_about_position(clicks, ticker):
        if not any(clicks or []) or not ticker:
            return no_update, no_update, no_update
        return True, 'ai', _ASK_COACH_TEMPLATE.format(ticker=ticker)


    def _mode_btn_style(active: bool) -> dict:
        # Constant fontWeight (600) across active/inactive prevents width jitter
        # between states. minWidth ensures "Preset" and "AI" occupy identical
        # space so the pill never reshuffles when the user toggles modes.
        base = {
            'padding':        '6px 16px',
            'fontSize':       '12px',
            'fontWeight':     '600',
            'minWidth':       '62px',
            'textAlign':      'center',
            'lineHeight':     '1.4',
            'border':         'none',
            'outline':        'none',
            'borderRadius':   '5px',
            'letterSpacing': '0.02em',
            'fontFamily':     'inherit',
            'cursor':         'pointer',
            'boxSizing':      'border-box',
            'transition':     'background 120ms ease, color 120ms ease',
        }
        if active:
            return {**base, 'color': COLOR_SURFACE_WHITE, 'background': COLOR_TEXT_STRONG}
        return {**base, 'color': COLOR_TEXT_DIM, 'background': 'transparent'}


    @app.callback(
        Output('coach-panel', 'children'),
        Input('coach-open', 'data'),
        Input('coach-mode', 'data'),
        Input('coach-active-id', 'data'),
        Input('coach-api-key', 'data'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        State('coach-chat-history', 'data'),
        State('coach-prefill', 'data'),
        State('portfolio-data', 'data'),
        State('market-intel-data', 'data'),
        State('valuation-data', 'data'),
    )
    def render_coach(is_open, mode, active_id, key, threads, active_thread_id,
                     chat_history, prefill, port, intel, val):
        if not is_open:
            return None

        mode = mode or 'preset'

        # ── Header: title left, mode toggle + close button right ──────────────────
        toggle = html.Div([
            html.Button("Preset", id='coach-mode-preset-btn',
                        style=_mode_btn_style(mode == 'preset')),
            html.Button("AI", id='coach-mode-ai-btn',
                        style=_mode_btn_style(mode == 'ai')),
        ], style={'display': 'flex', 'gap': '2px', 'padding': '3px',
                  'background': COLOR_SURFACE, 'borderRadius': '7px'})

        header = html.Div([
            html.Div([
                html.Span("✨", style={'fontSize': '18px', 'marginRight': '8px'}),
                html.Span("Portfolio coach",
                          style={'fontSize': '16px', 'fontWeight': '600', 'color': COLOR_TEXT_STRONG}),
            ], style={'display': 'flex', 'alignItems': 'center'}),
            html.Div([
                toggle,
                html.Button("✕", id='coach-close-btn', title="Close", style={
                    'width': '32px', 'height': '28px', 'background': COLOR_SURFACE_WHITE,
                    'border': '0.5px solid #ddd', 'borderRadius': '6px',
                    'cursor': 'pointer', 'fontSize': '14px', 'color': COLOR_TEXT_DIM,
                    'padding': '0', 'lineHeight': '1', 'marginLeft': '10px',
                }),
            ], style={'display': 'flex', 'alignItems': 'center'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'alignItems': 'center', 'marginBottom': '14px',
                  'paddingBottom': '14px', 'borderBottom': '0.5px solid #ebebeb'})

        provider    = ai_provider.detect_provider(key or '')
        key_present = bool(provider)

        children: list = [header]

        # Hidden ids registered once per branch below so callbacks (regenerate,
        # edit, new-thread) always have a target even when the relevant widget
        # isn't visible. suppress_callback_exceptions lets Dash tolerate missing
        # ids, but pattern-matching callbacks are happier with them present.
        _hidden_always = html.Div([
            html.Button(id='coach-regenerate-btn', n_clicks=0, style={'display': 'none'}),
            html.Button(id='coach-edit-btn',       n_clicks=0, style={'display': 'none'}),
            html.Button(id='coach-new-thread-btn', n_clicks=0, style={'display': 'none'}),
            html.Div(id='coach-tabs-row', style={'display': 'none'}),
        ], style={'display': 'none'})

        if mode == 'preset':
            # ── Preset: grid of clickable question chips + answer box ─────────────
            def _preset_chip_style(selected: bool) -> dict:
                base = {
                    'padding': '10px 14px', 'fontSize': '13px', 'cursor': 'pointer',
                    'borderRadius': '10px', 'textAlign': 'left', 'lineHeight': '1.35',
                    'transition': 'all 120ms ease', 'fontWeight': '500',
                }
                if selected:
                    return {**base, 'background': COLOR_TEXT_STRONG, 'color': COLOR_SURFACE_WHITE,
                            'border': '0.5px solid #111'}
                return {**base, 'background': COLOR_SURFACE_WHITE, 'color': COLOR_TEXT_SLATE,
                        'border': '0.5px solid #e5e7eb'}

            children.append(html.Div([
                html.Button(
                    s['label'],
                    id={'type': 'coach-preset-btn', 'index': s['id']},
                    n_clicks=0,
                    className='coach-chip',
                    style=_preset_chip_style(s['id'] == active_id),
                )
                for s in SCENARIOS
            ], style={'display': 'grid',
                      'gridTemplateColumns': 'repeat(auto-fill, minmax(220px, 1fr))',
                      'gap': '8px'}))

            if active_id:
                children.append(html.Div(
                    render_scenario(active_id, port, intel, val),
                    style={'marginTop': '14px'},
                ))
            # Hidden AI-only ids (keep registered for callbacks)
            children.append(html.Div([
                dcc.Input(id='coach-key-input', type='password', style={'display': 'none'}),
                html.Button(id='coach-save-key-btn',  style={'display': 'none'}),
                html.Button(id='coach-clear-key-btn', style={'display': 'none'}),
                dcc.Input(id='coach-input', type='text', style={'display': 'none'}),
                html.Button(id='coach-send-btn',        style={'display': 'none'}),
                html.Button(id='coach-clear-chat-btn',  style={'display': 'none'}),
                html.Div(id='coach-chat-output', style={'display': 'none'}),
            ], style={'display': 'none'}))
            children.append(_hidden_always)

        elif not key_present:
            # ── AI mode without key: key-entry form ───────────────────────────────
            children.append(html.Div([
                html.Div([
                    dcc.Input(
                        id='coach-key-input', type='password', value='',
                        placeholder='Paste API key: sk-ant-… / xai-… / sk-…',
                        n_submit=0,
                        style={**_COACH_INPUT, 'marginBottom': '0', 'flex': '1'},
                    ),
                    html.Button("Save", id='coach-save-key-btn',
                                style={**_COACH_BTN_PRIMARY, 'marginLeft': '8px'}),
                ], style={'display': 'flex', 'alignItems': 'stretch'}),
                html.P("Stored in your browser only — never uploaded.",
                       style={'color': COLOR_TEXT_FAINT, 'fontSize': '12px',
                              'margin': '6px 0 0', 'lineHeight': '1.5'}),
            ]))
            children.append(html.Div([
                html.Button(id='coach-clear-key-btn',   style={'display': 'none'}),
                dcc.Input(id='coach-input', type='text', style={'display': 'none'}),
                html.Button(id='coach-send-btn',        style={'display': 'none'}),
                html.Button(id='coach-clear-chat-btn',  style={'display': 'none'}),
                html.Div(id='coach-chat-output',        style={'display': 'none'}),
            ], style={'display': 'none'}))
            children.append(_hidden_always)

        else:
            # ── AI mode with key: full chat UI ────────────────────────────────────
            # Thread tabs row — its children are rendered by a separate callback
            # (render_thread_tabs) so sending a message doesn't rebuild the whole
            # panel (which would unmount the chat output mid-request).
            # When there are no threads yet, collapse the whole row (including its
            # border) so the panel doesn't show an empty gap above the status bar.
            has_threads = bool(threads)
            tabs_row = html.Div([
                html.Div(id='coach-tabs-row', style={
                    'display': 'flex', 'overflowX': 'auto', 'flex': '1',
                    'alignItems': 'center', 'gap': '0',
                    'scrollbarWidth': 'thin',
                }),
                html.Button("＋ New", id='coach-new-thread-btn', n_clicks=0, style={
                    'background': COLOR_SURFACE_WHITE, 'color': COLOR_TEXT_STRONG,
                    'border': '0.5px solid #e5e7eb', 'borderRadius': '999px',
                    'padding': '4px 12px', 'fontSize': '12px',
                    'cursor': 'pointer', 'marginLeft': '8px', 'fontWeight': '500',
                    'whiteSpace': 'nowrap',
                }),
            ], style={
                'display': 'flex' if has_threads else 'none',
                'alignItems': 'center',
                'marginBottom': '10px', 'paddingBottom': '10px',
                'borderBottom': '0.5px solid #f0f0f0',
            })

            # Status bar: connection chip + clear chat + clear key
            has_history = bool(chat_history)
            status_bar = html.Div([
                html.Span([
                    html.Span("●", style={'color': COLOR_GOOD, 'marginRight': '6px'}),
                    f"{ai_provider.provider_label(provider)} connected",
                ], style={'fontSize': '12px', 'color': COLOR_GOOD,
                          'background': COLOR_GOOD_BG, 'padding': '3px 10px',
                          'borderRadius': '999px',
                          'border': '0.5px solid #bbf7d0'}),
                html.Div([
                    html.Button("Clear chat", id='coach-clear-chat-btn', n_clicks=0,
                                style={**_COACH_BTN, 'padding': '3px 10px',
                                       'fontSize': '12px', 'marginRight': '6px',
                                       'opacity': '1' if has_history else '0.4',
                                       'pointerEvents': 'auto' if has_history else 'none'}),
                    html.Button("Clear key", id='coach-clear-key-btn', n_clicks=0,
                                style={**_COACH_BTN, 'padding': '3px 10px',
                                       'fontSize': '12px'}),
                ]),
            ], style={'display': 'flex', 'justifyContent': 'space-between',
                      'alignItems': 'center', 'marginBottom': '10px'})

            # Chat scroll area (updated by render_chat callback on new messages)
            chat_area = html.Div(
                _chat_bubbles(chat_history or [], None),
                id='coach-chat-output',
                className='coach-chat-output',
                style={'maxHeight': '420px',
                       'overflowY': 'auto', 'padding': '12px',
                       'background': COLOR_SURFACE_SOFT,
                       'border': '0.5px solid #ebebeb', 'borderRadius': '10px',
                       'marginBottom': '10px'},
            )

            # Input row: text box + send button (Enter to send)
            input_row = html.Div([
                dcc.Input(
                    id='coach-input', type='text', value=prefill or '', n_submit=0,
                    placeholder='Press enter to send',
                    autoComplete='off', debounce=False,
                    style={'flex': '1', 'padding': '10px 14px', 'fontSize': '14px',
                           'lineHeight': '1.5', 'boxSizing': 'border-box',
                           'border': '0.5px solid #ddd', 'borderRadius': '10px',
                           'background': COLOR_SURFACE_WHITE, 'color': COLOR_TEXT_STRONG,
                           'transition': 'border-color 120ms ease'},
                ),
                html.Button("Send ↑", id='coach-send-btn', n_clicks=0,
                            className='coach-send-btn',
                            style={**_COACH_BTN_PRIMARY, 'marginLeft': '8px',
                                   'padding': '11px 18px', 'fontSize': '13px',
                                   'fontWeight': '600', 'borderRadius': '10px'}),
            ], className='coach-input-row',
               style={'display': 'flex', 'alignItems': 'stretch'})

            children.append(tabs_row)
            children.append(status_bar)
            children.append(chat_area)
            children.append(input_row)

            # Hidden key-input ids (keep registered)
            children.append(html.Div([
                dcc.Input(id='coach-key-input', type='password',
                          value=key or '', style={'display': 'none'}),
                html.Button(id='coach-save-key-btn', style={'display': 'none'}),
            ], style={'display': 'none'}))

        return html.Div(
            children,
            id='coach-panel-card',
            className='coach-panel-card',
            style={**CARD, 'marginTop': '14px'},
        )

