/**
 * TemplatesTab — email / SMS template manager inside the Email & SMTP settings card.
 *
 * Layout:
 *  LEFT  — template list, grouped by group_name (Orders / Account / Branding / SMS)
 *  RIGHT — editor pane: enabled toggle, subject, variable inserter, Quill or textarea,
 *          live preview, save/reset/test actions.
 *
 * All data flows through TanStack Query hooks from features/emailTemplates/hooks.js.
 * No server state is duplicated into Zustand.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { motion } from 'framer-motion';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Mail,
  MessageSquare,
  RefreshCw,
  RotateCcw,
  Save,
  Send,
} from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { RichTextEditor } from '@/components/ui/RichTextEditor.jsx';
import { cn } from '@/lib/utils.js';
import { fadeIn, scaleIn } from '@/lib/motion.js';
import {
  useEmailTemplates,
  useEmailTemplate,
  useUpdateTemplate,
  usePreviewTemplate,
  useTestTemplate,
  useResetTemplate,
} from './hooks.js';

// ─── Group ordering ────────────────────────────────────────────────────────────

const GROUP_ORDER = ['orders', 'account', 'branding', 'sms'];
const GROUP_LABELS = {
  orders:   'Orders',
  account:  'Account',
  branding: 'Branding',
  sms:      'SMS',
};

// ─── Small helpers ─────────────────────────────────────────────────────────────

function EnabledDot({ enabled }) {
  return (
    <span
      className={cn(
        'mt-1 size-2 shrink-0 rounded-full',
        enabled ? 'bg-success' : 'bg-ink-tertiary',
      )}
      aria-label={enabled ? 'enabled' : 'disabled'}
    />
  );
}

// ─── TemplateList (left pane) ─────────────────────────────────────────────────

function TemplateList({ items, selectedKey, onSelect, isLoading }) {
  // Group items by group_name preserving GROUP_ORDER. Hooks must run
  // unconditionally, so this sits above the isLoading early return.
  const grouped = useMemo(() => {
    const map = {};
    for (const t of items || []) {
      (map[t.group_name] ||= []).push(t);
    }
    return GROUP_ORDER
      .filter((g) => map[g]?.length > 0)
      .map((g) => ({ group: g, templates: map[g] }));
  }, [items]);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2 p-3">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-14 rounded-sm" />
        ))}
      </div>
    );
  }

  return (
    <nav aria-label="Email templates" className="flex flex-col gap-4 p-3">
      {grouped.map(({ group, templates }) => (
        <div key={group}>
          <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
            {GROUP_LABELS[group] || group}
          </p>
          <div className="flex flex-col gap-0.5">
            {templates.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => onSelect(t.key)}
                aria-current={selectedKey === t.key ? 'true' : undefined}
                className={cn(
                  'flex w-full items-start gap-2.5 rounded-sm px-2.5 py-2.5 text-left text-sm transition-colors focus-visible:focus-ring',
                  selectedKey === t.key
                    ? 'bg-accent/12 text-accent'
                    : 'text-ink-secondary hover:bg-fill hover:text-ink-primary',
                )}
              >
                <EnabledDot enabled={t.is_enabled} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium leading-tight text-ink-primary">
                    {t.name}
                  </p>
                  {t.description && (
                    <p className="mt-0.5 truncate text-[11px] leading-tight text-ink-tertiary">
                      {t.description}
                    </p>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}

// ─── ResultBanner (mirrors TestSendBanner in AdminSettingsPage) ────────────────

function ResultBanner({ pending, result, kind = 'message' }) {
  if (pending) {
    return <p className="mt-3 text-xs text-ink-tertiary">Sending {kind}…</p>;
  }
  if (!result) return null;
  return (
    <motion.div
      variants={scaleIn}
      initial="hidden"
      animate="show"
      className={cn(
        'mt-3 flex items-start gap-2 rounded-sm border px-3.5 py-3 text-xs',
        result.ok
          ? 'border-success/30 bg-success/8 text-success'
          : 'border-danger/30 bg-danger/8 text-danger',
      )}
    >
      {result.ok ? (
        <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      ) : (
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      )}
      <span>{result.message}</span>
    </motion.div>
  );
}

// ─── VariableMenu ─────────────────────────────────────────────────────────────

function VariableMenu({ variables = [], onInsert }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    function handler(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  if (!variables.length) return null;

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'inline-flex items-center gap-1.5 rounded-sm border border-line-subtle bg-bg-elevated',
          'px-3 py-1.5 text-xs font-medium text-ink-secondary transition-colors',
          'hover:border-line-strong hover:text-ink-primary focus-visible:focus-ring',
        )}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span>{`{ }`}</span>
        Insert variable
        <ChevronDown className="size-3.5" aria-hidden="true" />
      </button>

      {open && (
        <motion.div
          variants={scaleIn}
          initial="hidden"
          animate="show"
          role="listbox"
          aria-label="Template variables"
          className={cn(
            'absolute left-0 top-full z-20 mt-1.5 min-w-[220px] overflow-hidden',
            'rounded-sm border border-line-subtle bg-bg-elevated shadow-md',
          )}
        >
          <div className="max-h-64 overflow-y-auto py-1">
            {variables.map((v) => (
              <button
                key={v.token}
                role="option"
                aria-selected="false"
                type="button"
                onClick={() => {
                  onInsert(`{{ ${v.token} }}`);
                  setOpen(false);
                }}
                className={cn(
                  'flex w-full flex-col gap-0.5 px-3 py-2 text-left transition-colors',
                  'hover:bg-fill focus-visible:focus-ring',
                )}
              >
                <span className="font-mono text-[11px] text-accent">
                  {`{{ ${v.token} }}`}
                </span>
                <span className="text-xs text-ink-secondary">{v.label}</span>
                {v.sample && (
                  <span className="text-[10px] text-ink-tertiary">
                    e.g. {v.sample}
                  </span>
                )}
              </button>
            ))}
          </div>
        </motion.div>
      )}
    </div>
  );
}

// ─── SmsTextarea — textarea with cursor-aware variable insert ─────────────────

function SmsTextarea({ value, onChange, variables, maxLength = 1600 }) {
  const taRef = useRef(null);

  function insertVariable(token) {
    const ta = taRef.current;
    if (!ta) return;
    const start = ta.selectionStart ?? ta.value.length;
    const end = ta.selectionEnd ?? start;
    const next = ta.value.slice(0, start) + token + ta.value.slice(end);
    onChange(next);
    // Restore cursor after inserted token.
    requestAnimationFrame(() => {
      ta.setSelectionRange(start + token.length, start + token.length);
      ta.focus();
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-ink-secondary">Message body</span>
        <VariableMenu variables={variables} onInsert={insertVariable} />
      </div>
      <div className="relative">
        <textarea
          ref={taRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          maxLength={maxLength}
          rows={6}
          spellCheck={false}
          className={cn(
            'block w-full resize-y rounded-sm border border-line-subtle bg-bg-sunken',
            'px-3 py-2.5 font-mono text-sm text-ink-primary placeholder:text-ink-tertiary',
            'transition-[border-color] duration-200 hover:border-line-strong',
            'focus-visible:border-accent focus-visible:outline-none',
            'focus-visible:[box-shadow:var(--accent-glow)]',
          )}
          placeholder="SMS body — use {{ variable }} tokens"
        />
      </div>
      <p className="text-right text-[11px] text-ink-tertiary">
        {value.length} / {maxLength} chars
      </p>
    </div>
  );
}

// ─── EmailEditor ──────────────────────────────────────────────────────────────

function EmailEditor({ detail, onSaved }) {
  const rteRef = useRef(null);
  const updateMutation = useUpdateTemplate();
  const previewMutation = usePreviewTemplate();
  const testMutation = useTestTemplate();
  const resetMutation = useResetTemplate();

  const isLayout = detail.key === '_layout' || detail.key?.endsWith('_layout');

  const [enabled, setEnabled] = useState(detail.is_enabled);
  const [subject, setSubject] = useState(detail.subject ?? '');
  const [bodyHtml, setBodyHtml] = useState(detail.body_html ?? '');
  const [bodyDelta, setBodyDelta] = useState(
    detail.body_design ?? null,
  );
  const [testTo, setTestTo] = useState('');
  const [testResult, setTestResult] = useState(null);
  const [previewData, setPreviewData] = useState(null);
  const previewTimerRef = useRef(null);

  // Re-seed when the selected template changes.
  useEffect(() => {
    setEnabled(detail.is_enabled);
    setSubject(detail.subject ?? '');
    setBodyHtml(detail.body_html ?? '');
    setBodyDelta(detail.body_design ?? null);
    setTestResult(null);
    setPreviewData(null);
  }, [detail.key]); // eslint-disable-line react-hooks/exhaustive-deps

  // Dirty check — compare against server detail.
  const dirty =
    enabled !== detail.is_enabled ||
    (!isLayout && subject !== (detail.subject ?? '')) ||
    bodyHtml !== (detail.body_html ?? '');

  // Debounced live preview.
  useEffect(() => {
    clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(() => {
      const draft = {};
      if (!isLayout) draft.subject = subject;
      draft.body_html = bodyHtml;
      previewMutation.mutate(
        { key: detail.key, draft },
        {
          onSuccess: (data) => setPreviewData(data),
        },
      );
    }, 400);
    return () => clearTimeout(previewTimerRef.current);
  }, [subject, bodyHtml, detail.key]); // eslint-disable-line react-hooks/exhaustive-deps

  function handleEditorChange({ html, delta }) {
    setBodyHtml(html);
    setBodyDelta(delta);
  }

  async function handleSave() {
    const payload = {};
    if (enabled !== detail.is_enabled) payload.is_enabled = enabled;
    if (!isLayout && subject !== (detail.subject ?? '')) payload.subject = subject;
    if (bodyHtml !== (detail.body_html ?? '')) {
      payload.body_html = bodyHtml;
      // body_design is a native JSON object (Quill Delta) — the backend column
      // is JSON and the schema expects an object, so send it as-is (no stringify).
      if (bodyDelta) payload.body_design = bodyDelta;
    }
    if (Object.keys(payload).length === 0) return;
    updateMutation.mutate({ key: detail.key, payload }, { onSuccess: onSaved });
  }

  function handleReset() {
    if (
      !window.confirm(
        'Reset this template to its built-in default? Your customisations will be lost.',
      )
    )
      return;
    resetMutation.mutate(detail.key, {
      onSuccess: (data) => {
        setEnabled(data.is_enabled);
        setSubject(data.subject ?? '');
        setBodyHtml(data.body_html ?? '');
        setBodyDelta(data.body_design ?? null);
        onSaved?.();
      },
    });
  }

  function handleTest() {
    setTestResult(null);
    testMutation.mutate(
      { key: detail.key, to: testTo },
      {
        onSuccess: (d) =>
          setTestResult({ ok: true, message: d.detail || 'Sent.' }),
        onError: (e) =>
          setTestResult({
            ok: false,
            message:
              e?.response?.data?.error?.message ||
              e?.response?.data?.detail ||
              'Send failed.',
          }),
      },
    );
  }

  function insertVariable(token) {
    rteRef.current?.insertVariable(token);
  }

  return (
    <motion.div variants={fadeIn} initial="hidden" animate="show" className="flex flex-col gap-5">
      {/* Enabled toggle */}
      <label className={cn(
        'flex cursor-pointer items-center gap-3 rounded-sm border px-4 py-3 text-sm transition-all duration-150',
        enabled
          ? 'border-success/30 bg-success/8'
          : 'border-line-subtle bg-bg-sunken hover:border-line-strong hover:bg-fill',
      )}>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="size-4 rounded-sm border border-line-subtle bg-bg-elevated text-accent"
        />
        <span className="font-medium text-ink-primary">
          {enabled ? 'Enabled — this template will send' : 'Disabled — no messages will be sent'}
        </span>
      </label>

      {/* Subject (hidden for _layout templates) */}
      {!isLayout && (
        <Input
          label="Subject line"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder={detail.default_subject || 'Email subject…'}
        />
      )}

      {/* Variable inserter + Quill body */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-ink-secondary">Body (HTML)</span>
          <VariableMenu variables={detail.variables || []} onInsert={insertVariable} />
        </div>
        {/*
         * Quill attaches its toolbar above the editor div. The rte-wrapper
         * class in global CSS (or tailwind config) styles the container.
         * min-h keeps the editor from collapsing before Quill boots.
         */}
        <div className="overflow-hidden rounded-sm border border-line-subtle bg-white">
          <RichTextEditor
            ref={rteRef}
            value={bodyHtml}
            delta={bodyDelta}
            onChange={handleEditorChange}
          />
        </div>
      </div>

      {/* Live preview */}
      <div className="rounded-sm border border-line-subtle bg-bg-sunken">
        <div className="flex items-center justify-between border-b border-line-subtle px-4 py-2.5">
          <p className="text-xs font-semibold text-ink-secondary">Live preview</p>
          {previewMutation.isPending && (
            <RefreshCw className="size-3.5 animate-spin text-ink-tertiary" aria-hidden="true" />
          )}
        </div>
        {previewData ? (
          <>
            {previewData.subject && (
              <p className="border-b border-line-subtle px-4 py-2 text-xs font-medium text-ink-primary">
                <span className="mr-1.5 text-ink-tertiary">Subject:</span>
                {previewData.subject}
              </p>
            )}
            <iframe
              key={previewData.html}
              srcDoc={previewData.html}
              sandbox=""
              title="Email preview"
              className="h-[420px] w-full"
            />
          </>
        ) : (
          <div className="flex h-36 items-center justify-center text-xs text-ink-tertiary">
            Preview loads as you type…
          </div>
        )}
      </div>

      {/* Action row */}
      <div className="flex flex-wrap items-center gap-3 border-t border-line-subtle pt-4">
        <Button
          onClick={handleSave}
          loading={updateMutation.isPending}
          disabled={!dirty || updateMutation.isPending}
          size="sm"
        >
          <Save className="size-4" aria-hidden="true" />
          Save changes
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleReset}
          loading={resetMutation.isPending}
        >
          <RotateCcw className="size-3.5" aria-hidden="true" />
          Reset to default
        </Button>

        {updateMutation.isSuccess && !dirty && (
          <motion.span
            variants={fadeIn}
            initial="hidden"
            animate="show"
            className="flex items-center gap-1 text-xs text-success"
          >
            <CheckCircle2 className="size-3.5" aria-hidden="true" />
            Saved
          </motion.span>
        )}
        {updateMutation.isError && (
          <span className="flex items-center gap-1 text-xs text-danger">
            <AlertTriangle className="size-3.5" aria-hidden="true" />
            {updateMutation.error?.response?.data?.detail || 'Save failed.'}
          </span>
        )}
      </div>

      {/* Test send */}
      <div className="rounded-sm border border-line-subtle bg-bg-sunken p-4">
        <CardHeader
          title="Send a test"
          className="mb-3 border-none px-0 py-0"
          action={<Badge tone="info" size="sm">Test</Badge>}
        />
        <p className="mb-3 text-xs text-ink-secondary">
          Delivers a preview using sample variable values to any address.
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            type="email"
            placeholder="you@example.com"
            value={testTo}
            onChange={(e) => setTestTo(e.target.value)}
            aria-label="Test recipient address"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={handleTest}
            loading={testMutation.isPending}
            disabled={!testTo}
          >
            <Send className="size-4" aria-hidden="true" />
            Send test
          </Button>
        </div>
        <ResultBanner
          pending={testMutation.isPending}
          result={testResult}
          kind="test email"
        />
      </div>
    </motion.div>
  );
}

// ─── SmsEditor ────────────────────────────────────────────────────────────────

function SmsEditor({ detail, onSaved }) {
  const updateMutation = useUpdateTemplate();
  const previewMutation = usePreviewTemplate();
  const testMutation = useTestTemplate();
  const resetMutation = useResetTemplate();

  const [enabled, setEnabled] = useState(detail.is_enabled);
  const [body, setBody] = useState(detail.body_html ?? '');
  const [testTo, setTestTo] = useState('');
  const [testResult, setTestResult] = useState(null);
  const [previewText, setPreviewText] = useState('');
  const previewTimerRef = useRef(null);

  useEffect(() => {
    setEnabled(detail.is_enabled);
    setBody(detail.body_html ?? '');
    setTestResult(null);
    setPreviewText('');
  }, [detail.key]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = enabled !== detail.is_enabled || body !== (detail.body_html ?? '');

  // Debounced preview.
  useEffect(() => {
    clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(() => {
      previewMutation.mutate(
        { key: detail.key, draft: { body_html: body } },
        { onSuccess: (d) => setPreviewText(d.html ?? '') },
      );
    }, 400);
    return () => clearTimeout(previewTimerRef.current);
  }, [body, detail.key]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSave() {
    const payload = {};
    if (enabled !== detail.is_enabled) payload.is_enabled = enabled;
    if (body !== (detail.body_html ?? '')) payload.body_html = body;
    if (Object.keys(payload).length === 0) return;
    updateMutation.mutate({ key: detail.key, payload }, { onSuccess: onSaved });
  }

  function handleReset() {
    if (!window.confirm('Reset this SMS template to its default?')) return;
    resetMutation.mutate(detail.key, {
      onSuccess: (data) => {
        setEnabled(data.is_enabled);
        setBody(data.body_html ?? '');
        onSaved?.();
      },
    });
  }

  function handleTest() {
    setTestResult(null);
    testMutation.mutate(
      { key: detail.key, to: testTo },
      {
        onSuccess: (d) =>
          setTestResult({ ok: true, message: d.detail || 'Sent.' }),
        onError: (e) =>
          setTestResult({
            ok: false,
            message:
              e?.response?.data?.error?.message ||
              e?.response?.data?.detail ||
              'Send failed.',
          }),
      },
    );
  }

  return (
    <motion.div variants={fadeIn} initial="hidden" animate="show" className="flex flex-col gap-5">
      {/* Enabled toggle */}
      <label className={cn(
        'flex cursor-pointer items-center gap-3 rounded-sm border px-4 py-3 text-sm transition-all duration-150',
        enabled
          ? 'border-success/30 bg-success/8'
          : 'border-line-subtle bg-bg-sunken hover:border-line-strong hover:bg-fill',
      )}>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="size-4 rounded-sm border border-line-subtle bg-bg-elevated text-accent"
        />
        <span className="font-medium text-ink-primary">
          {enabled ? 'Enabled — this SMS will send' : 'Disabled — no SMS will be sent'}
        </span>
      </label>

      {/* Textarea body with variable inserter */}
      <SmsTextarea
        value={body}
        onChange={setBody}
        variables={detail.variables || []}
      />

      {/* SMS bubble preview */}
      <div className="rounded-sm border border-line-subtle bg-bg-sunken p-4">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-semibold text-ink-secondary">Preview</p>
          {previewMutation.isPending && (
            <RefreshCw className="size-3.5 animate-spin text-ink-tertiary" aria-hidden="true" />
          )}
        </div>
        {previewText ? (
          <div className="flex justify-start">
            <div className={cn(
              'max-w-[80%] rounded-2xl rounded-tl-none bg-fill px-4 py-3',
              'text-sm text-ink-primary whitespace-pre-wrap break-words shadow-sm',
            )}>
              {previewText}
            </div>
          </div>
        ) : (
          <p className="text-xs text-ink-tertiary">Preview loads as you type…</p>
        )}
      </div>

      {/* Action row */}
      <div className="flex flex-wrap items-center gap-3 border-t border-line-subtle pt-4">
        <Button
          onClick={handleSave}
          loading={updateMutation.isPending}
          disabled={!dirty || updateMutation.isPending}
          size="sm"
        >
          <Save className="size-4" aria-hidden="true" />
          Save changes
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleReset}
          loading={resetMutation.isPending}
        >
          <RotateCcw className="size-3.5" aria-hidden="true" />
          Reset to default
        </Button>

        {updateMutation.isSuccess && !dirty && (
          <motion.span
            variants={fadeIn}
            initial="hidden"
            animate="show"
            className="flex items-center gap-1 text-xs text-success"
          >
            <CheckCircle2 className="size-3.5" aria-hidden="true" />
            Saved
          </motion.span>
        )}
        {updateMutation.isError && (
          <span className="flex items-center gap-1 text-xs text-danger">
            <AlertTriangle className="size-3.5" aria-hidden="true" />
            {updateMutation.error?.response?.data?.detail || 'Save failed.'}
          </span>
        )}
      </div>

      {/* Test send */}
      <div className="rounded-sm border border-line-subtle bg-bg-sunken p-4">
        <CardHeader
          title="Send a test SMS"
          className="mb-3 border-none px-0 py-0"
          action={<Badge tone="info" size="sm">Test</Badge>}
        />
        <p className="mb-3 text-xs text-ink-secondary">
          Sends a test to any phone number via the active SMS backend.
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            type="tel"
            placeholder="+14155551234"
            value={testTo}
            onChange={(e) => setTestTo(e.target.value)}
            aria-label="Test recipient phone number"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={handleTest}
            loading={testMutation.isPending}
            disabled={!testTo}
          >
            <Send className="size-4" aria-hidden="true" />
            Send test
          </Button>
        </div>
        <ResultBanner
          pending={testMutation.isPending}
          result={testResult}
          kind="test SMS"
        />
      </div>
    </motion.div>
  );
}

// ─── EditorPane (right pane) ──────────────────────────────────────────────────

function EditorPane({ selectedKey }) {
  const { data: detail, isLoading, isError } = useEmailTemplate(selectedKey);

  const handleSaved = useCallback(() => {
    // The mutation's onSuccess already updates the query cache; nothing extra needed.
  }, []);

  if (!selectedKey) {
    return (
      <div className="flex h-full min-h-[360px] flex-col items-center justify-center gap-3 rounded-sm border border-line-subtle bg-bg-sunken p-8 text-center">
        <Mail className="size-8 text-ink-tertiary" aria-hidden="true" />
        <p className="text-sm font-medium text-ink-secondary">
          Select a template on the left to edit it
        </p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4 rounded-sm border border-line-subtle bg-bg-elevated p-6">
        <Skeleton className="h-6 w-48 rounded-sm" />
        <Skeleton className="h-11 rounded-sm" />
        <Skeleton className="h-11 rounded-sm" />
        <Skeleton className="h-64 rounded-sm" />
        <Skeleton className="h-48 rounded-sm" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex h-48 items-center justify-center rounded-sm border border-danger/30 bg-danger/4 p-6">
        <p className="text-sm text-danger">Failed to load template.</p>
      </div>
    );
  }

  return (
    <div className="rounded-sm border border-line-subtle bg-bg-elevated">
      {/* Pane header */}
      <div className="flex items-center gap-3 border-b border-line-subtle px-5 py-4">
        <span className="grid size-8 shrink-0 place-items-center rounded-sm bg-accent/12 text-accent">
          {detail.channel === 'sms' ? (
            <MessageSquare className="size-4" aria-hidden="true" />
          ) : (
            <Mail className="size-4" aria-hidden="true" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink-primary">{detail.name}</p>
          {detail.description && (
            <p className="truncate text-xs text-ink-tertiary">{detail.description}</p>
          )}
        </div>
        <Badge
          tone={detail.channel === 'sms' ? 'info' : 'accent'}
          size="sm"
          className="uppercase tracking-wide"
        >
          {detail.channel}
        </Badge>
      </div>

      <div className="p-5">
        {detail.channel === 'sms' ? (
          <SmsEditor key={detail.key} detail={detail} onSaved={handleSaved} />
        ) : (
          <EmailEditor key={detail.key} detail={detail} onSaved={handleSaved} />
        )}
      </div>
    </div>
  );
}

// ─── TemplatesTab (exported) ──────────────────────────────────────────────────

export function TemplatesTab() {
  const { data, isLoading } = useEmailTemplates();
  const [selectedKey, setSelectedKey] = useState(null);

  // Auto-select the first template once the list loads.
  useEffect(() => {
    if (selectedKey || !data?.items?.length) return;
    setSelectedKey(data.items[0].key);
  }, [data, selectedKey]);

  return (
    <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
      {/* Left: template list */}
      <Card flat className="lg:max-h-[780px] lg:overflow-y-auto">
        <TemplateList
          items={data?.items}
          selectedKey={selectedKey}
          onSelect={setSelectedKey}
          isLoading={isLoading}
        />
      </Card>

      {/* Right: editor pane */}
      <EditorPane selectedKey={selectedKey} />
    </div>
  );
}

