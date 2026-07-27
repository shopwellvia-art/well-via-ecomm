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
  LayoutTemplate,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';
import { fadeIn } from '@/lib/motion.js';
import {
  useFooterConfig,
  useUpdateFooterConfig,
  useUploadFooterLogo,
} from '@/features/footer/hooks.js';
import {
  FOOTER_DEFAULTS,
  TRUST_ICON_NAMES,
  SOCIAL_ICON_NAMES,
  BOTTOM_ICON_NAMES,
} from '@/features/footer/defaults.js';

// ---------------------------------------------------------------------------
// Small reusable helpers
// ---------------------------------------------------------------------------

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

/** A list of plain string items with add/remove. */
function StringList({ items, onChange, placeholder = 'Enter value', addLabel = 'Add item' }) {
  function update(i, val) {
    const next = [...items];
    next[i] = val;
    onChange(next);
  }
  function remove(i) {
    onChange(items.filter((_, idx) => idx !== i));
  }
  function add() {
    onChange([...items, '']);
  }
  return (
    <div className="flex flex-col gap-2">
      {items.map((item, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type="text"
            value={item}
            onChange={(e) => update(i, e.target.value)}
            placeholder={placeholder}
            className="h-9 flex-1 rounded-md border border-line-subtle bg-bg-sunken px-3 text-sm text-ink-primary placeholder:text-ink-tertiary hover:border-line-strong focus-visible:border-accent focus-visible:outline-none focus-visible:focus-ring transition-colors"
          />
          <button
            type="button"
            onClick={() => remove(i)}
            aria-label="Remove"
            className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
          >
            <Trash2 className="size-3.5" aria-hidden="true" />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-sm text-accent transition-colors hover:bg-accent/10 focus-visible:focus-ring"
      >
        <Plus className="size-4" aria-hidden="true" />
        {addLabel}
      </button>
    </div>
  );
}

/** A row inside a list of objects with a delete button. */
function ListRow({ children, onRemove, className }) {
  return (
    <div
      className={cn(
        'relative rounded-lg border border-line-subtle bg-bg-sunken p-4',
        className,
      )}
    >
      <button
        type="button"
        onClick={onRemove}
        aria-label="Remove row"
        className="absolute right-2.5 top-2.5 grid size-7 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
      >
        <Trash2 className="size-3.5" aria-hidden="true" />
      </button>
      {children}
    </div>
  );
}

// Shared inline input class for raw <input> elements inside row editors
const rowInput =
  'h-9 w-full rounded-md border border-line-subtle bg-bg-elevated px-3 text-sm text-ink-primary placeholder:text-ink-tertiary hover:border-line-strong focus-visible:border-accent focus-visible:outline-none focus-visible:focus-ring transition-colors';

const rowSelect = cn(rowInput, 'appearance-none');

// ---------------------------------------------------------------------------
// Section editors
// ---------------------------------------------------------------------------

function LogoUploader({ brand, onChange }) {
  const upload = useUploadFooterLogo();
  const [err, setErr] = useState('');

  async function onPick(e) {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-picking the same file
    if (!file) return;
    setErr('');
    try {
      const { url } = await upload.mutateAsync(file);
      onChange({ ...brand, logo_url: url });
    } catch (e2) {
      setErr(
        e2?.response?.data?.error?.message ||
          e2?.response?.data?.detail ||
          'Could not upload the logo. Please try a PNG, SVG or WebP under 15 MB.',
      );
    }
  }

  return (
    <div className="sm:col-span-2">
      <p className="mb-2 text-xs font-medium text-ink-secondary">Logo</p>
      <div className="flex flex-wrap items-start gap-4">
        {/* Preview */}
        <div className="grid size-20 shrink-0 place-items-center overflow-hidden rounded-lg border border-line-subtle bg-bg-sunken">
          {brand.logo_url ? (
            <img
              src={brand.logo_url}
              alt="Brand logo preview"
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
            {brand.logo_url ? 'Replace logo' : 'Upload logo'}
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp,image/svg+xml,image/avif"
              onChange={onPick}
              disabled={upload.isPending}
              className="sr-only"
            />
          </label>
          {brand.logo_url && (
            <button
              type="button"
              onClick={() => onChange({ ...brand, logo_url: '' })}
              className="flex items-center gap-1.5 text-xs text-ink-tertiary transition-colors hover:text-danger focus-visible:focus-ring"
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
              Remove logo
            </button>
          )}
        </div>
      </div>
      <p className="mt-2 text-xs text-ink-tertiary">
        Shown in the navbar and footer. Use a transparent PNG or SVG. Leave empty to show the brand name.
      </p>
      {err && <p className="mt-1 text-xs text-danger">{err}</p>}
    </div>
  );
}

function BrandEditor({ brand, onChange }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <LogoUploader brand={brand} onChange={onChange} />
      <Input
        label="Brand name"
        value={brand.name}
        onChange={(e) => onChange({ ...brand, name: e.target.value })}
        placeholder="Lumen"
        helper="Used as alt text / fallback when no logo is uploaded."
      />
      <div className="sm:col-span-2">
        <Textarea
          label="Tagline"
          value={brand.tagline}
          onChange={(e) => onChange({ ...brand, tagline: e.target.value })}
          rows={2}
          placeholder="Modern essentials, thoughtfully sourced…"
        />
      </div>
    </div>
  );
}

function NewsletterEditor({ newsletter, onChange }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-line-subtle bg-bg-sunken px-4 py-3 transition-colors hover:border-line-strong">
          <input
            type="checkbox"
            checked={newsletter.enabled !== false}
            onChange={(e) => onChange({ ...newsletter, enabled: e.target.checked })}
            className="size-4 rounded-sm border border-line-subtle bg-bg-elevated text-accent focus-visible:focus-ring"
          />
          <div>
            <span className="text-sm font-medium text-ink-primary">Newsletter sign-up enabled</span>
            <p className="mt-0.5 text-xs text-ink-tertiary">
              When disabled, the sign-up form is hidden from the footer.
            </p>
          </div>
        </label>
      </div>
      <Input
        label="Email placeholder"
        value={newsletter.placeholder}
        onChange={(e) => onChange({ ...newsletter, placeholder: e.target.value })}
        placeholder="you@example.com"
      />
      <Input
        label="Helper note"
        value={newsletter.note}
        onChange={(e) => onChange({ ...newsletter, note: e.target.value })}
        placeholder="No spam. Unsubscribe anytime."
        helper="Shown below the input before submission."
      />
      <div className="sm:col-span-2">
        <Input
          label="Success message"
          value={newsletter.success}
          onChange={(e) => onChange({ ...newsletter, success: e.target.value })}
          placeholder="You're on the list. Welcome to Lumen."
          helper="Shown after a valid email is submitted."
        />
      </div>
    </div>
  );
}

function TrustFeaturesEditor({ features, onChange }) {
  function updateRow(i, field, val) {
    const next = features.map((f, idx) => (idx === i ? { ...f, [field]: val } : f));
    onChange(next);
  }
  function removeRow(i) {
    onChange(features.filter((_, idx) => idx !== i));
  }
  function addRow() {
    onChange([...features, { icon: 'ShieldCheck', title: '', sub: '' }]);
  }

  return (
    <div className="flex flex-col gap-3">
      {features.map((f, i) => (
        <ListRow key={i} onRemove={() => removeRow(i)}>
          <div className="grid gap-3 pr-8 sm:grid-cols-3">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">Icon</label>
              <select
                value={f.icon}
                onChange={(e) => updateRow(i, 'icon', e.target.value)}
                className={rowSelect}
              >
                {TRUST_ICON_NAMES.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">Title</label>
              <input
                type="text"
                value={f.title}
                onChange={(e) => updateRow(i, 'title', e.target.value)}
                placeholder="Free Shipping"
                className={rowInput}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">Subtitle</label>
              <input
                type="text"
                value={f.sub}
                onChange={(e) => updateRow(i, 'sub', e.target.value)}
                placeholder="On orders over ₹500"
                className={rowInput}
              />
            </div>
          </div>
        </ListRow>
      ))}
      <button
        type="button"
        onClick={addRow}
        className="flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-sm text-accent transition-colors hover:bg-accent/10 focus-visible:focus-ring"
      >
        <Plus className="size-4" aria-hidden="true" />
        Add trust badge
      </button>
    </div>
  );
}

function LinkColumnsEditor({ columns, onChange }) {
  function updateColumn(ci, field, val) {
    onChange(columns.map((c, idx) => (idx === ci ? { ...c, [field]: val } : c)));
  }
  function removeColumn(ci) {
    onChange(columns.filter((_, idx) => idx !== ci));
  }
  function addColumn() {
    onChange([...columns, { title: '', links: [] }]);
  }
  function updateLink(ci, li, field, val) {
    const cols = columns.map((c, idx) => {
      if (idx !== ci) return c;
      return {
        ...c,
        links: c.links.map((l, lidx) => (lidx === li ? { ...l, [field]: val } : l)),
      };
    });
    onChange(cols);
  }
  function removeLink(ci, li) {
    const cols = columns.map((c, idx) => {
      if (idx !== ci) return c;
      return { ...c, links: c.links.filter((_, lidx) => lidx !== li) };
    });
    onChange(cols);
  }
  function addLink(ci) {
    const cols = columns.map((c, idx) => {
      if (idx !== ci) return c;
      return { ...c, links: [...c.links, { label: '', to: '/' }] };
    });
    onChange(cols);
  }

  return (
    <div className="flex flex-col gap-4">
      {columns.map((col, ci) => (
        <div key={ci} className="rounded-lg border border-line-subtle bg-bg-sunken p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <input
              type="text"
              value={col.title}
              onChange={(e) => updateColumn(ci, 'title', e.target.value)}
              placeholder="Column title"
              className={cn(rowInput, 'flex-1 font-medium')}
            />
            <button
              type="button"
              onClick={() => removeColumn(ci)}
              aria-label="Remove column"
              className="grid size-8 shrink-0 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
            >
              <Trash2 className="size-4" aria-hidden="true" />
            </button>
          </div>

          <div className="flex flex-col gap-2 pl-2">
            {col.links.map((link, li) => (
              <div key={li} className="flex items-center gap-2">
                <input
                  type="text"
                  value={link.label}
                  onChange={(e) => updateLink(ci, li, 'label', e.target.value)}
                  placeholder="Label"
                  className="h-8 flex-1 rounded-md border border-line-subtle bg-bg-elevated px-2.5 text-xs text-ink-primary placeholder:text-ink-tertiary hover:border-line-strong focus-visible:border-accent focus-visible:outline-none focus-visible:focus-ring transition-colors"
                />
                <input
                  type="text"
                  value={link.to}
                  onChange={(e) => updateLink(ci, li, 'to', e.target.value)}
                  placeholder="/path"
                  className="h-8 w-32 rounded-md border border-line-subtle bg-bg-elevated px-2.5 text-xs text-ink-primary placeholder:text-ink-tertiary hover:border-line-strong focus-visible:border-accent focus-visible:outline-none focus-visible:focus-ring transition-colors"
                />
                <button
                  type="button"
                  onClick={() => removeLink(ci, li)}
                  aria-label="Remove link"
                  className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
                >
                  <Trash2 className="size-3.5" aria-hidden="true" />
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() => addLink(ci)}
              className="mt-1 flex items-center gap-1 self-start text-xs text-accent transition-colors hover:underline focus-visible:focus-ring"
            >
              <Plus className="size-3" aria-hidden="true" />
              Add link
            </button>
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={addColumn}
        className="flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-sm text-accent transition-colors hover:bg-accent/10 focus-visible:focus-ring"
      >
        <Plus className="size-4" aria-hidden="true" />
        Add column
      </button>
    </div>
  );
}

function AddressBlockEditor({ value, onChange }) {
  return (
    <div className="flex flex-col gap-3">
      <Input
        label="Section heading"
        value={value.heading}
        onChange={(e) => onChange({ ...value, heading: e.target.value })}
      />
      <div>
        <p className="mb-2 text-sm font-medium text-ink-secondary">Address lines</p>
        <StringList
          items={value.lines}
          onChange={(lines) => onChange({ ...value, lines })}
          placeholder="Address line"
          addLabel="Add line"
        />
      </div>
    </div>
  );
}

function RegisteredOfficeEditor({ value, onChange }) {
  function updatePhone(i, field, val) {
    const phones = value.phones.map((p, idx) => (idx === i ? { ...p, [field]: val } : p));
    onChange({ ...value, phones });
  }
  function removePhone(i) {
    onChange({ ...value, phones: value.phones.filter((_, idx) => idx !== i) });
  }
  function addPhone() {
    onChange({ ...value, phones: [...value.phones, { display: '', tel: '' }] });
  }

  return (
    <div className="flex flex-col gap-4">
      <Input
        label="Section heading"
        value={value.heading}
        onChange={(e) => onChange({ ...value, heading: e.target.value })}
      />
      <div>
        <p className="mb-2 text-sm font-medium text-ink-secondary">Address lines</p>
        <StringList
          items={value.lines}
          onChange={(lines) => onChange({ ...value, lines })}
          placeholder="Address line"
          addLabel="Add line"
        />
      </div>
      <Input
        label="CIN"
        value={value.cin}
        onChange={(e) => onChange({ ...value, cin: e.target.value })}
        placeholder="U51109KA2026PTC066107"
        helper="Company Identification Number — leave blank to hide."
      />
      <div>
        <p className="mb-2 text-sm font-medium text-ink-secondary">Phone numbers</p>
        <div className="flex flex-col gap-2">
          {value.phones.map((p, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                type="text"
                value={p.display}
                onChange={(e) => updatePhone(i, 'display', e.target.value)}
                placeholder="044-4561 4700"
                aria-label="Display number"
                className={cn(rowInput, 'flex-1')}
              />
              <input
                type="text"
                value={p.tel}
                onChange={(e) => updatePhone(i, 'tel', e.target.value)}
                placeholder="+914445614700"
                aria-label="tel: link value"
                className={cn(rowInput, 'flex-1')}
              />
              <button
                type="button"
                onClick={() => removePhone(i)}
                aria-label="Remove phone"
                className="grid size-8 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={addPhone}
            className="flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-sm text-accent transition-colors hover:bg-accent/10 focus-visible:focus-ring"
          >
            <Plus className="size-4" aria-hidden="true" />
            Add phone
          </button>
        </div>
      </div>
    </div>
  );
}

function SocialLinksEditor({ links, onChange }) {
  function updateRow(i, field, val) {
    onChange(links.map((l, idx) => (idx === i ? { ...l, [field]: val } : l)));
  }
  function removeRow(i) {
    onChange(links.filter((_, idx) => idx !== i));
  }
  function addRow() {
    onChange([...links, { icon: 'Facebook', label: '', href: 'https://' }]);
  }

  return (
    <div className="flex flex-col gap-3">
      {links.map((link, i) => (
        <ListRow key={i} onRemove={() => removeRow(i)}>
          <div className="grid gap-3 pr-8 sm:grid-cols-3">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">Icon</label>
              <select
                value={link.icon}
                onChange={(e) => updateRow(i, 'icon', e.target.value)}
                className={rowSelect}
              >
                {SOCIAL_ICON_NAMES.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">Label</label>
              <input
                type="text"
                value={link.label}
                onChange={(e) => updateRow(i, 'label', e.target.value)}
                placeholder="Facebook"
                className={rowInput}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">URL</label>
              <input
                type="url"
                value={link.href}
                onChange={(e) => updateRow(i, 'href', e.target.value)}
                placeholder="https://facebook.com/lumen"
                className={rowInput}
              />
            </div>
          </div>
        </ListRow>
      ))}
      <button
        type="button"
        onClick={addRow}
        className="flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-sm text-accent transition-colors hover:bg-accent/10 focus-visible:focus-ring"
      >
        <Plus className="size-4" aria-hidden="true" />
        Add social link
      </button>
    </div>
  );
}

function BottomLinksEditor({ links, onChange }) {
  function updateRow(i, field, val) {
    onChange(links.map((l, idx) => (idx === i ? { ...l, [field]: val } : l)));
  }
  function removeRow(i) {
    onChange(links.filter((_, idx) => idx !== i));
  }
  function addRow() {
    onChange([...links, { icon: 'Store', label: '', to: '/' }]);
  }

  return (
    <div className="flex flex-col gap-3">
      {links.map((link, i) => (
        <ListRow key={i} onRemove={() => removeRow(i)}>
          <div className="grid gap-3 pr-8 sm:grid-cols-3">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">Icon</label>
              <select
                value={link.icon}
                onChange={(e) => updateRow(i, 'icon', e.target.value)}
                className={rowSelect}
              >
                {BOTTOM_ICON_NAMES.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">Label</label>
              <input
                type="text"
                value={link.label}
                onChange={(e) => updateRow(i, 'label', e.target.value)}
                placeholder="Become a Seller"
                className={rowInput}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">Path</label>
              <input
                type="text"
                value={link.to}
                onChange={(e) => updateRow(i, 'to', e.target.value)}
                placeholder="/sell"
                className={rowInput}
              />
            </div>
          </div>
        </ListRow>
      ))}
      <button
        type="button"
        onClick={addRow}
        className="flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-sm text-accent transition-colors hover:bg-accent/10 focus-visible:focus-ring"
      >
        <Plus className="size-4" aria-hidden="true" />
        Add bottom link
      </button>
    </div>
  );
}

function PaymentMethodsEditor({ methods, onChange }) {
  return (
    <StringList
      items={methods}
      onChange={onChange}
      placeholder="e.g. VISA"
      addLabel="Add payment method"
    />
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminFooterPage() {
  const { data, isLoading, isError, refetch } = useFooterConfig();
  const update = useUpdateFooterConfig();

  const [draft, setDraft] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [savedAt, setSavedAt] = useState(false);

  // Hydrate local state from the server snapshot (or defaults on first load).
  useEffect(() => {
    if (draft !== null) return; // don't overwrite user edits on background re-fetch
    const source = data ?? FOOTER_DEFAULTS;
    // Deep clone so edits don't mutate the query cache.
    setDraft(JSON.parse(JSON.stringify(source)));
  }, [data, draft]);

  function set(key, value) {
    setDraft((d) => ({ ...d, [key]: value }));
    setSavedAt(false);
  }

  async function handleSave() {
    setSaveError(null);
    try {
      await update.mutateAsync(draft);
      setSavedAt(true);
      setTimeout(() => setSavedAt(false), 3000);
    } catch (err) {
      setSaveError(
        err?.response?.data?.error?.message ||
          err?.response?.data?.detail ||
          'Could not save footer config. Please try again.',
      );
    }
  }

  if (isLoading && !draft) {
    return (
      <AdminPage title="Footer" description="Loading footer configuration…">
        <div className="flex max-w-3xl flex-col gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      </AdminPage>
    );
  }

  if (isError && !draft) {
    return (
      <AdminPage title="Footer" description="Storefront footer configuration.">
        <div className="flex items-start gap-3 rounded-lg border border-danger/30 bg-danger/8 px-4 py-3 text-sm text-danger">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load footer config</p>
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
      title="Footer"
      description="Manage every section of the storefront footer. Changes are live immediately after saving."
    >
      {/* Sticky save toolbar */}
      <div className="sticky top-0 z-10 -mx-6 mb-6 flex items-center justify-between gap-4 border-b border-line-subtle bg-bg-elevated/95 px-6 py-3 backdrop-blur">
        <div className="flex items-center gap-2 min-w-0">
          <LayoutTemplate className="size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
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
        <SectionCard title="Brand" description="Name and tagline shown in the footer brand block.">
          <BrandEditor brand={draft.brand} onChange={(v) => set('brand', v)} />
        </SectionCard>

        <SectionCard title="Newsletter" description="Sign-up form copy and toggle.">
          <NewsletterEditor newsletter={draft.newsletter} onChange={(v) => set('newsletter', v)} />
        </SectionCard>

        <SectionCard
          title="Trust features"
          description="Badges shown at the top of the footer (e.g. Free Shipping, Easy Returns)."
        >
          <TrustFeaturesEditor
            features={draft.trust_features}
            onChange={(v) => set('trust_features', v)}
          />
        </SectionCard>

        <SectionCard
          title="Link columns"
          description="Navigation columns — each has a heading and a list of internal links."
          defaultOpen={false}
        >
          <LinkColumnsEditor columns={draft.link_columns} onChange={(v) => set('link_columns', v)} />
        </SectionCard>

        <SectionCard
          title="Mail Us"
          description="Address block shown below the link columns."
          defaultOpen={false}
        >
          <AddressBlockEditor value={draft.mail_us} onChange={(v) => set('mail_us', v)} />
        </SectionCard>

        <SectionCard
          title="Registered Office"
          description="Registered address, CIN, and telephone numbers."
          defaultOpen={false}
        >
          <RegisteredOfficeEditor
            value={draft.registered_office}
            onChange={(v) => set('registered_office', v)}
          />
        </SectionCard>

        <SectionCard title="Social links" description="Icon buttons linking to social profiles.">
          <SocialLinksEditor links={draft.social_links} onChange={(v) => set('social_links', v)} />
        </SectionCard>

        <SectionCard
          title="Bottom links"
          description="Utility links in the footer bottom bar (e.g. Become a Seller)."
        >
          <BottomLinksEditor links={draft.bottom_links} onChange={(v) => set('bottom_links', v)} />
        </SectionCard>

        <SectionCard
          title="Payment methods"
          description="Badge labels shown in the footer bottom bar."
        >
          <PaymentMethodsEditor
            methods={draft.payment_methods}
            onChange={(v) => set('payment_methods', v)}
          />
        </SectionCard>

        <SectionCard title="Copyright" description="Bottom bar copyright text.">
          <Input
            label="Copyright text"
            value={draft.copyright}
            onChange={(e) => set('copyright', e.target.value)}
            placeholder="© 2007–{year} Lumen.com"
            helper="Use {year} as a token — replaced with the current year at render time."
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
