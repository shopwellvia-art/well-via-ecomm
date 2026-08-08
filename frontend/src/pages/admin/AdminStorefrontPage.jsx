import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Check,
  AlertTriangle,
  Plus,
  Trash2,
  ChevronDown,
  ChevronUp,
  Upload,
  ImageOff,
  Loader2,
  Store,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';
import { fadeIn } from '@/lib/motion.js';
import {
  useStorefrontConfig,
  useUpdateStorefrontConfig,
  useUploadStorefrontImage,
} from '@/features/storefront-config/hooks.js';
import { STOREFRONT_DEFAULTS } from '@/features/storefront-config/defaults.js';

// ---------------------------------------------------------------------------
// Small reusable helpers
// ---------------------------------------------------------------------------

// Canonical section labels — keyed so renames in defaults flow through.
const SECTION_LABELS = Object.fromEntries(
  STOREFRONT_DEFAULTS.homepage_sections.map((s) => [s.key, s.label]),
);

function SectionCard({ title, description, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card flat className="border border-line-subtle">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-5 py-4 text-left focus-visible:focus-ring"
      >
        <div>
          <p className="text-sm font-semibold text-ink-primary">{title}</p>
          {description && (
            <p className="mt-0.5 text-xs text-ink-tertiary">{description}</p>
          )}
        </div>
        <span className="grid size-6 place-items-center rounded text-ink-tertiary transition-colors hover:bg-fill">
          {open
            ? <ChevronUp className="size-4" aria-hidden="true" />
            : <ChevronDown className="size-4" aria-hidden="true" />}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="content"
            variants={fadeIn}
            initial="hidden"
            animate="show"
            exit="hidden"
            className="border-t border-line-subtle"
          >
            <CardBody>{children}</CardBody>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}

// Shared inline input class for raw <input> elements inside row editors
const rowInput =
  'h-9 w-full rounded-md border border-line-subtle bg-bg-elevated px-3 text-sm text-ink-primary placeholder:text-ink-tertiary hover:border-line-strong focus-visible:border-accent focus-visible:outline-none focus-visible:focus-ring transition-colors disabled:cursor-not-allowed disabled:opacity-50';

function VisibleSwitch({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200',
        'focus-visible:focus-ring',
        checked ? 'bg-accent' : 'bg-fill-strong',
      )}
    >
      <span
        className={cn(
          'pointer-events-none block size-4 rounded-full bg-white shadow transition-transform duration-200',
          checked ? 'translate-x-4' : 'translate-x-0',
        )}
      />
    </button>
  );
}

function MoveButton({ direction, disabled, onClick }) {
  const Icon = direction === 'up' ? ChevronUp : ChevronDown;
  return (
    <button
      type="button"
      aria-label={direction === 'up' ? 'Move up' : 'Move down'}
      disabled={disabled}
      onClick={onClick}
      className="grid size-8 shrink-0 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
}

function moveItem(list, i, direction) {
  const j = direction === 'up' ? i - 1 : i + 1;
  if (j < 0 || j >= list.length) return list;
  const next = [...list];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

// ---------------------------------------------------------------------------
// Section editors
// ---------------------------------------------------------------------------

function ImageUploader({ label, value, onChange, helper, uploadLabel, replaceLabel }) {
  const upload = useUploadStorefrontImage();
  const [err, setErr] = useState('');

  async function onPick(e) {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-picking the same file
    if (!file) return;
    setErr('');
    try {
      const { url } = await upload.mutateAsync(file);
      onChange(url);
    } catch (e2) {
      setErr(
        e2?.response?.data?.error?.message ||
          e2?.response?.data?.detail ||
          'Could not upload the image. Please try a PNG, JPEG or WebP under 15 MB.',
      );
    }
  }

  return (
    <div>
      <p className="mb-2 text-xs font-medium text-ink-secondary">{label}</p>
      <div className="flex flex-wrap items-start gap-4">
        {/* Preview */}
        <div className="grid size-20 shrink-0 place-items-center overflow-hidden rounded-lg border border-line-subtle bg-bg-sunken">
          {value ? (
            <img
              src={value}
              alt={`${label} preview`}
              className="size-full object-contain p-2"
            />
          ) : (
            <ImageOff className="size-6 text-ink-tertiary" aria-hidden="true" />
          )}
        </div>

        <div className="flex flex-col items-start gap-2">
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-line-subtle bg-bg-elevated px-3 py-2 text-sm text-ink-primary transition-colors hover:border-line-strong focus-within:focus-ring">
            {upload.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Upload className="size-4" aria-hidden="true" />
            )}
            {value ? replaceLabel : uploadLabel}
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp,image/avif"
              onChange={onPick}
              disabled={upload.isPending}
              className="sr-only"
            />
          </label>
          {value && (
            <button
              type="button"
              onClick={() => onChange('')}
              className="flex items-center gap-1.5 text-xs text-ink-tertiary transition-colors hover:text-danger focus-visible:focus-ring"
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
              Reset to default
            </button>
          )}
        </div>
      </div>
      <p className="mt-2 text-xs text-ink-tertiary">{helper}</p>
      {err && <p className="mt-1 text-xs text-danger">{err}</p>}
    </div>
  );
}

function BrandingEditor({ draft, set }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <Input
          label="Site title"
          value={draft.site_title}
          onChange={(e) => set('site_title', e.target.value)}
          placeholder="Wellvia — Wellness Redefined"
          helper="Browser tab title of the public site."
        />
      </div>
      <Input
        label="Brand name"
        value={draft.brand_name}
        onChange={(e) => set('brand_name', e.target.value)}
        placeholder="WELLVIA"
        helper="Shown in the header when no logo is uploaded, and as logo alt text."
      />
      <Input
        label="Tagline"
        value={draft.tagline}
        onChange={(e) => set('tagline', e.target.value)}
        placeholder="Wellness Redefined"
      />
      <ImageUploader
        label="Logo"
        value={draft.logo_url}
        onChange={(v) => set('logo_url', v)}
        uploadLabel="Upload logo"
        replaceLabel="Replace logo"
        helper="Use a transparent PNG or WebP. Leave empty to show the brand name. Images go live after Save."
      />
      <ImageUploader
        label="Favicon"
        value={draft.favicon_url}
        onChange={(v) => set('favicon_url', v)}
        uploadLabel="Upload favicon"
        replaceLabel="Replace favicon"
        helper="Must be a square PNG or WebP — SVG and ICO are not accepted. Images go live after Save."
      />
    </div>
  );
}

function NavItemsEditor({ items, onChange }) {
  function updateRow(i, field, val) {
    onChange(items.map((it, idx) => (idx === i ? { ...it, [field]: val } : it)));
  }
  function removeRow(i) {
    onChange(items.filter((_, idx) => idx !== i));
  }
  function addRow() {
    onChange([...items, { label: '', to: '/', visible: true }]);
  }

  return (
    <div className="flex flex-col gap-3">
      {items.map((item, i) => (
        <div key={i} className="rounded-lg border border-line-subtle bg-bg-sunken p-3">
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={item.label}
              onChange={(e) => updateRow(i, 'label', e.target.value)}
              placeholder="Label"
              aria-label="Link label"
              className={cn(rowInput, 'flex-1')}
            />
            <input
              type="text"
              value={item.to}
              onChange={(e) => updateRow(i, 'to', e.target.value)}
              placeholder="/path"
              aria-label="Link path"
              disabled={!!item.megaMenu}
              className={cn(rowInput, 'w-36 sm:w-44')}
            />
            <VisibleSwitch
              checked={item.visible !== false}
              onChange={(v) => updateRow(i, 'visible', v)}
              label={item.visible !== false ? 'Hide link' : 'Show link'}
            />
            <MoveButton
              direction="up"
              disabled={i === 0}
              onClick={() => onChange(moveItem(items, i, 'up'))}
            />
            <MoveButton
              direction="down"
              disabled={i === items.length - 1}
              onClick={() => onChange(moveItem(items, i, 'down'))}
            />
            <button
              type="button"
              onClick={() => removeRow(i)}
              aria-label="Remove link"
              disabled={!!item.megaMenu}
              className="grid size-8 shrink-0 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring disabled:pointer-events-none disabled:opacity-30"
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
            </button>
          </div>
          {item.megaMenu && (
            <p className="mt-1.5 text-xs text-ink-tertiary">
              Shop menu — path managed by the mega menu.
            </p>
          )}
        </div>
      ))}
      <button
        type="button"
        onClick={addRow}
        className="flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-sm text-accent transition-colors hover:bg-accent/10 focus-visible:focus-ring"
      >
        <Plus className="size-4" aria-hidden="true" />
        Add link
      </button>
    </div>
  );
}

function HomepageSectionsEditor({ sections, onChange }) {
  function updateRow(i, field, val) {
    onChange(sections.map((s, idx) => (idx === i ? { ...s, [field]: val } : s)));
  }

  return (
    <div className="flex flex-col gap-3">
      {sections.map((section, i) => (
        <div key={section.key} className="rounded-lg border border-line-subtle bg-bg-sunken p-3">
          <div className="flex items-center gap-2">
            <p className="flex-1 truncate text-sm font-medium text-ink-primary">
              {SECTION_LABELS[section.key] ?? section.label}
            </p>
            <VisibleSwitch
              checked={section.visible !== false}
              onChange={(v) => updateRow(i, 'visible', v)}
              label={section.visible !== false ? 'Hide section' : 'Show section'}
            />
            <MoveButton
              direction="up"
              disabled={i === 0}
              onClick={() => onChange(moveItem(sections, i, 'up'))}
            />
            <MoveButton
              direction="down"
              disabled={i === sections.length - 1}
              onClick={() => onChange(moveItem(sections, i, 'down'))}
            />
          </div>
          {section.key === 'hero' ? (
            <p className="mt-1.5 text-xs text-ink-tertiary">
              Hero copy is managed in Hero slides.
            </p>
          ) : (
            <div className="mt-2">
              <input
                type="text"
                value={section.title}
                onChange={(e) => updateRow(i, 'title', e.target.value)}
                placeholder="Heading override (optional)"
                aria-label={`Heading override for ${SECTION_LABELS[section.key] ?? section.label}`}
                className={rowInput}
              />
            </div>
          )}
        </div>
      ))}
      <p className="text-xs text-ink-tertiary">
        The eight sections are fixed — reorder and hide them, or override a heading.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminStorefrontPage() {
  const { data, isLoading, isError, refetch } = useStorefrontConfig();
  const update = useUpdateStorefrontConfig();

  const [draft, setDraft] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [savedAt, setSavedAt] = useState(false);

  // Hydrate local state from the server snapshot (or defaults on first load).
  useEffect(() => {
    if (draft !== null) return; // don't overwrite user edits on background re-fetch
    const source = data ?? STOREFRONT_DEFAULTS;
    // Deep clone so edits don't mutate the query cache.
    setDraft(JSON.parse(JSON.stringify(source)));
  }, [data, draft]);

  function set(key, value) {
    setDraft((d) => ({ ...d, [key]: value }));
    setSavedAt(false);
  }

  async function handleSave() {
    setSaveError(null);
    if (draft.nav_items.some((it) => !(it.label || '').trim())) {
      setSaveError('Every navigation link needs a label.');
      return;
    }
    if (draft.nav_items.some((it) => !(it.to || '').startsWith('/'))) {
      setSaveError('Navigation paths must start with "/".');
      return;
    }
    try {
      await update.mutateAsync(draft);
      setSavedAt(true);
      setTimeout(() => setSavedAt(false), 3000);
    } catch (err) {
      setSaveError(
        err?.response?.data?.error?.message ||
          err?.response?.data?.detail ||
          'Could not save storefront config. Please try again.',
      );
    }
  }

  if (isLoading && !draft) {
    return (
      <AdminPage title="Storefront" description="Loading storefront configuration…">
        <div className="flex max-w-3xl flex-col gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      </AdminPage>
    );
  }

  if (isError && !draft) {
    return (
      <AdminPage
        title="Storefront"
        description="Brand identity, navigation and homepage layout of the public site."
      >
        <div className="flex items-start gap-3 rounded-lg border border-danger/30 bg-danger/8 px-4 py-3 text-sm text-danger">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load storefront config</p>
            <p className="mt-0.5 text-xs opacity-80">
              Showing defaults. Save to persist your changes.
            </p>
            <button
              type="button"
              onClick={() => refetch()}
              className="mt-2 text-xs underline hover:no-underline focus-visible:focus-ring"
            >
              Retry
            </button>
          </div>
        </div>
      </AdminPage>
    );
  }

  if (!draft) return null;

  return (
    <AdminPage
      title="Storefront"
      description="Brand identity, navigation and homepage layout of the public site."
    >
      {/* Sticky save toolbar */}
      <div className="sticky top-0 z-10 -mx-6 mb-6 flex items-center justify-between gap-4 border-b border-line-subtle bg-bg-elevated/95 px-6 py-3 backdrop-blur">
        <div className="flex items-center gap-2 min-w-0">
          <Store className="size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
          <p className="truncate text-sm text-ink-secondary">
            Edit any section below, then save all at once.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {savedAt && (
            <span className="flex items-center gap-1.5 text-xs text-success">
              <Check className="size-4" aria-hidden="true" />
              Saved
            </span>
          )}
          {saveError && (
            <span className="flex items-center gap-1.5 text-xs text-danger">
              <AlertTriangle className="size-4" aria-hidden="true" />
              <span className="hidden sm:inline">{saveError}</span>
            </span>
          )}
          <Button onClick={handleSave} loading={update.isPending}>
            Save all changes
          </Button>
        </div>
      </div>

      <div className="flex max-w-3xl flex-col gap-4">
        <SectionCard
          title="Branding"
          description="Site title, brand name, tagline, logo and favicon."
        >
          <BrandingEditor draft={draft} set={set} />
        </SectionCard>

        <SectionCard
          title="Navigation"
          description="Links in the storefront header — reorder, hide or rename them."
        >
          <NavItemsEditor items={draft.nav_items} onChange={(v) => set('nav_items', v)} />
        </SectionCard>

        <SectionCard
          title="Homepage sections"
          description="Order and visibility of the homepage sections."
        >
          <HomepageSectionsEditor
            sections={draft.homepage_sections}
            onChange={(v) => set('homepage_sections', v)}
          />
        </SectionCard>
      </div>

      {/* Bottom save button for long pages */}
      <div className="mt-8 flex max-w-3xl items-center justify-end gap-3">
        {savedAt && (
          <span className="flex items-center gap-1.5 text-xs text-success">
            <Check className="size-4" aria-hidden="true" />
            Saved
          </span>
        )}
        {saveError && <p className="text-xs text-danger">{saveError}</p>}
        <Button onClick={handleSave} loading={update.isPending}>
          Save all changes
        </Button>
      </div>
    </AdminPage>
  );
}
