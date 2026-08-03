import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Check,
  AlertTriangle,
  Plus,
  Trash2,
  ChevronDown,
  ChevronUp,
  FileText,
  Globe,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, fadeIn } from '@/lib/motion.js';
import { useSitePages, useUpdateSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS, PAGE_ICON_NAMES } from '@/features/site-pages/defaults.js';

// ---------------------------------------------------------------------------
// Reusable primitives
// ---------------------------------------------------------------------------

const inputCls =
  'h-9 w-full rounded-md border border-line-subtle bg-bg-elevated px-3 text-sm text-ink-primary placeholder:text-ink-tertiary hover:border-line-strong focus-visible:border-accent focus-visible:outline-none focus-visible:focus-ring transition-colors';

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
          {description && <p className="mt-0.5 text-xs text-ink-tertiary">{description}</p>}
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

function FieldLabel({ children, required }) {
  return (
    <label className="mb-1.5 block text-xs font-medium text-ink-secondary">
      {children}
      {required && <span className="ml-0.5 text-danger" aria-hidden="true">*</span>}
    </label>
  );
}

function AddButton({ onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-sm text-accent transition-colors hover:bg-accent/10 focus-visible:focus-ring"
    >
      <Plus className="size-4" aria-hidden="true" />
      {children}
    </button>
  );
}

function RemoveButton({ onClick, label = 'Remove' }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="grid size-8 shrink-0 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
    >
      <Trash2 className="size-3.5" aria-hidden="true" />
    </button>
  );
}

/** A list of plain strings with add/remove. */
function StringList({ items = [], onChange, placeholder = 'Enter value', addLabel = 'Add item' }) {
  return (
    <div className="flex flex-col gap-2">
      {items.map((item, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type="text"
            value={item}
            onChange={(e) => onChange(items.map((v, idx) => (idx === i ? e.target.value : v)))}
            placeholder={placeholder}
            className={inputCls}
          />
          <RemoveButton
            label="Remove item"
            onClick={() => onChange(items.filter((_, idx) => idx !== i))}
          />
        </div>
      ))}
      <AddButton onClick={() => onChange([...items, ''])}>{addLabel}</AddButton>
    </div>
  );
}

/**
 * Generic editor for a list of objects.
 * `fields` describes each editable property:
 *   { key, label, type: 'text'|'textarea'|'icon'|'lines', placeholder, full }
 */
function ObjectList({ items = [], onChange, fields, template, addLabel }) {
  function updateRow(i, key, val) {
    onChange(items.map((it, idx) => (idx === i ? { ...it, [key]: val } : it)));
  }
  function removeRow(i) {
    onChange(items.filter((_, idx) => idx !== i));
  }

  return (
    <div className="flex flex-col gap-3">
      {items.map((item, i) => (
        <div
          key={i}
          className="relative rounded-lg border border-line-subtle bg-bg-sunken p-4 pr-12"
        >
          <div className="absolute right-2.5 top-2.5">
            <RemoveButton label="Remove row" onClick={() => removeRow(i)} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {fields.map((f) => (
              <div
                key={f.key}
                className={cn(
                  f.full || f.type === 'textarea' || f.type === 'lines' ? 'sm:col-span-2' : '',
                )}
              >
                <FieldLabel>{f.label}</FieldLabel>
                {f.type === 'textarea' ? (
                  <textarea
                    rows={f.rows || 3}
                    value={item[f.key] ?? ''}
                    onChange={(e) => updateRow(i, f.key, e.target.value)}
                    placeholder={f.placeholder}
                    className={cn(inputCls, 'h-auto resize-y py-2')}
                  />
                ) : f.type === 'icon' ? (
                  <select
                    value={item[f.key] ?? ''}
                    onChange={(e) => updateRow(i, f.key, e.target.value)}
                    className={cn(inputCls, 'appearance-none')}
                  >
                    {PAGE_ICON_NAMES.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                ) : f.type === 'lines' ? (
                  <StringList
                    items={item[f.key] || []}
                    onChange={(val) => updateRow(i, f.key, val)}
                    placeholder={f.placeholder}
                    addLabel={f.addLabel || 'Add line'}
                  />
                ) : (
                  <input
                    type="text"
                    value={item[f.key] ?? ''}
                    onChange={(e) => updateRow(i, f.key, e.target.value)}
                    placeholder={f.placeholder}
                    className={inputCls}
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
      <AddButton onClick={() => onChange([...items, { ...template }])}>{addLabel}</AddButton>
    </div>
  );
}

function HeroEditor({ hero, onChange }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Input
        label="Eyebrow"
        value={hero.eyebrow}
        onChange={(e) => onChange({ ...hero, eyebrow: e.target.value })}
        placeholder="Our Story"
      />
      <Input
        label="Title"
        value={hero.title}
        onChange={(e) => onChange({ ...hero, title: e.target.value })}
        placeholder="Page headline"
      />
      <div className="sm:col-span-2">
        <Textarea
          label="Subtitle"
          rows={2}
          value={hero.subtitle}
          onChange={(e) => onChange({ ...hero, subtitle: e.target.value })}
        />
      </div>
    </div>
  );
}

function ProseEditor({ value, onChange }) {
  return (
    <div className="flex flex-col gap-4">
      <Input
        label="Heading"
        value={value.heading}
        onChange={(e) => onChange({ ...value, heading: e.target.value })}
      />
      <Textarea
        label="Body"
        rows={5}
        value={value.body}
        onChange={(e) => onChange({ ...value, body: e.target.value })}
        helper="Separate paragraphs with a blank line."
      />
    </div>
  );
}

function EnabledToggle({ enabled, onChange }) {
  return (
    <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-line-subtle bg-bg-sunken px-4 py-3 transition-colors hover:border-line-strong">
      <input
        type="checkbox"
        checked={enabled !== false}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 rounded-sm border border-line-subtle bg-bg-elevated text-accent focus-visible:focus-ring"
      />
      <div>
        <span className="text-sm font-medium text-ink-primary">Page published</span>
        <p className="mt-0.5 text-xs text-ink-tertiary">
          When off, visitors see an "unavailable" message.
        </p>
      </div>
      <div className="ml-auto">
        {enabled !== false
          ? <Badge tone="success" dot>Live</Badge>
          : <Badge tone="neutral">Draft</Badge>}
      </div>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Per-page editors
// ---------------------------------------------------------------------------

function AboutEditor({ page, set }) {
  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="Header" description="Eyebrow, title and subtitle.">
        <EnabledToggle enabled={page.enabled} onChange={(v) => set('enabled', v)} />
        <div className="mt-4">
          <HeroEditor hero={page.hero} onChange={(v) => set('hero', v)} />
        </div>
      </SectionCard>
      <SectionCard title="Story paragraphs" defaultOpen={false}>
        <StringList
          items={page.intro}
          onChange={(v) => set('intro', v)}
          placeholder="A paragraph of your story"
          addLabel="Add paragraph"
        />
      </SectionCard>
      <SectionCard title="Stats" description="Headline numbers." defaultOpen={false}>
        <ObjectList
          items={page.stats}
          onChange={(v) => set('stats', v)}
          fields={[
            { key: 'value', label: 'Value', placeholder: '10M+' },
            { key: 'label', label: 'Label', placeholder: 'Happy customers' },
          ]}
          template={{ value: '', label: '' }}
          addLabel="Add stat"
        />
      </SectionCard>
      <SectionCard title="Values" description="Icon + title + text cards." defaultOpen={false}>
        <ObjectList
          items={page.values}
          onChange={(v) => set('values', v)}
          fields={[
            { key: 'icon', label: 'Icon', type: 'icon' },
            { key: 'title', label: 'Title', placeholder: 'Customer obsessed' },
            { key: 'text', label: 'Text', type: 'textarea', rows: 2 },
          ]}
          template={{ icon: 'Sparkles', title: '', text: '' }}
          addLabel="Add value"
        />
      </SectionCard>
      <SectionCard title="Mission" defaultOpen={false}>
        <ProseEditor value={page.mission} onChange={(v) => set('mission', v)} />
      </SectionCard>
    </div>
  );
}

function ContactEditor({ page, set }) {
  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="Header">
        <EnabledToggle enabled={page.enabled} onChange={(v) => set('enabled', v)} />
        <div className="mt-4">
          <HeroEditor hero={page.hero} onChange={(v) => set('hero', v)} />
        </div>
        <div className="mt-4">
          <Input
            label="Banner image"
            value={page.hero?.image ?? ''}
            placeholder="/hero-contact.png"
            onChange={(e) => set('hero', { ...page.hero, image: e.target.value })}
          />
          <p className="mt-1.5 text-xs text-ink-tertiary">
            The default banner already has &ldquo;Contact Us&rdquo; and its sub-line
            printed into the image, which is why the title fields above are empty —
            filling them would show the same words twice. Point this at a banner
            without text and the title/subtitle will render over it.
          </p>
        </div>
        <div className="mt-4">
          <Textarea
            label="Intro"
            rows={2}
            value={page.intro}
            onChange={(e) => set('intro', e.target.value)}
          />
        </div>
      </SectionCard>
      <SectionCard title="Contact methods" description="Email, phone, chat, address cards.">
        <ObjectList
          items={page.methods}
          onChange={(v) => set('methods', v)}
          fields={[
            { key: 'icon', label: 'Icon', type: 'icon' },
            { key: 'title', label: 'Title', placeholder: 'Email us' },
            { key: 'detail', label: 'Detail', placeholder: 'support@shopwellvia.in' },
            {
              key: 'href',
              label: 'Link (mailto:/tel:/https:)',
              placeholder: 'mailto:support@shopwellvia.in',
            },
          ]}
          template={{ icon: 'Mail', title: '', detail: '', href: '' }}
          addLabel="Add method"
        />
      </SectionCard>
      <SectionCard title="Enquiry form" description="Copy for the on-page contact form.">
        <div className="grid gap-4">
          <Input
            label="Heading"
            value={page.form.heading}
            onChange={(e) => set('form', { ...page.form, heading: e.target.value })}
          />
          <Input
            label="Note"
            value={page.form.note}
            onChange={(e) => set('form', { ...page.form, note: e.target.value })}
          />
          <Input
            label="Success message"
            value={page.form.success}
            onChange={(e) => set('form', { ...page.form, success: e.target.value })}
          />
        </div>
      </SectionCard>
      <SectionCard
        title="Support hours"
        description="Shown under the enquiry form. Clear both fields to hide the block."
      >
        <div className="grid gap-4">
          <Input
            label="Hours"
            value={page.hours ?? ''}
            placeholder="Monday – Saturday (9:00 AM – 6:00 PM IST)"
            onChange={(e) => set('hours', e.target.value)}
          />
          <Input
            label="Response note"
            value={page.response_note ?? ''}
            placeholder="We aim to respond within 24–48 business hours."
            onChange={(e) => set('response_note', e.target.value)}
          />
        </div>
      </SectionCard>
      <SectionCard title="Offices" defaultOpen={false}>
        <ObjectList
          items={page.offices}
          onChange={(v) => set('offices', v)}
          fields={[
            { key: 'city', label: 'City / label', placeholder: 'Bengaluru (HQ)' },
            { key: 'lines', label: 'Address lines', type: 'lines', placeholder: 'Address line' },
          ]}
          template={{ city: '', lines: [] }}
          addLabel="Add office"
        />
      </SectionCard>
    </div>
  );
}

function CareersEditor({ page, set }) {
  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="Header">
        <EnabledToggle enabled={page.enabled} onChange={(v) => set('enabled', v)} />
        <div className="mt-4">
          <HeroEditor hero={page.hero} onChange={(v) => set('hero', v)} />
        </div>
        <div className="mt-4">
          <Textarea
            label="Intro"
            rows={2}
            value={page.intro}
            onChange={(e) => set('intro', e.target.value)}
          />
        </div>
      </SectionCard>
      <SectionCard title="Perks">
        <ObjectList
          items={page.perks}
          onChange={(v) => set('perks', v)}
          fields={[
            { key: 'icon', label: 'Icon', type: 'icon' },
            { key: 'title', label: 'Title', placeholder: 'People first' },
            { key: 'text', label: 'Text', type: 'textarea', rows: 2 },
          ]}
          template={{ icon: 'Gift', title: '', text: '' }}
          addLabel="Add perk"
        />
      </SectionCard>
      <SectionCard title="Open roles">
        <ObjectList
          items={page.openings}
          onChange={(v) => set('openings', v)}
          fields={[
            { key: 'title', label: 'Role title', placeholder: 'Senior Frontend Engineer' },
            { key: 'department', label: 'Department', placeholder: 'Engineering' },
            { key: 'location', label: 'Location', placeholder: 'Bengaluru / Remote' },
            { key: 'type', label: 'Type', placeholder: 'Full-time' },
            {
              key: 'url',
              label: 'Apply link',
              placeholder: 'mailto:careers@shopwellvia.in',
              full: true,
            },
          ]}
          template={{ title: '', department: '', location: '', type: 'Full-time', url: '' }}
          addLabel="Add role"
        />
      </SectionCard>
      <SectionCard title="Culture" defaultOpen={false}>
        <ProseEditor value={page.culture} onChange={(v) => set('culture', v)} />
      </SectionCard>
    </div>
  );
}

function StoriesEditor({ page, set }) {
  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="Header">
        <EnabledToggle enabled={page.enabled} onChange={(v) => set('enabled', v)} />
        <div className="mt-4">
          <HeroEditor hero={page.hero} onChange={(v) => set('hero', v)} />
        </div>
        <div className="mt-4">
          <Textarea
            label="Intro"
            rows={2}
            value={page.intro}
            onChange={(e) => set('intro', e.target.value)}
          />
        </div>
      </SectionCard>
      <SectionCard title="Posts">
        <ObjectList
          items={page.posts}
          onChange={(v) => set('posts', v)}
          fields={[
            { key: 'title', label: 'Title', placeholder: 'Story title', full: true },
            { key: 'excerpt', label: 'Excerpt', type: 'textarea', rows: 2 },
            { key: 'category', label: 'Category', placeholder: 'People' },
            { key: 'date', label: 'Date (YYYY-MM-DD)', placeholder: '2026-05-12' },
            { key: 'image', label: 'Image URL', placeholder: 'https://…' },
            { key: 'url', label: 'Article link', placeholder: 'https://…' },
          ]}
          template={{ title: '', excerpt: '', category: '', date: '', image: '', url: '' }}
          addLabel="Add post"
        />
      </SectionCard>
    </div>
  );
}

function PressEditor({ page, set }) {
  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="Header">
        <EnabledToggle enabled={page.enabled} onChange={(v) => set('enabled', v)} />
        <div className="mt-4">
          <HeroEditor hero={page.hero} onChange={(v) => set('hero', v)} />
        </div>
        <div className="mt-4">
          <Textarea
            label="Intro"
            rows={2}
            value={page.intro}
            onChange={(e) => set('intro', e.target.value)}
          />
        </div>
      </SectionCard>
      <SectionCard title="Releases & coverage">
        <ObjectList
          items={page.releases}
          onChange={(v) => set('releases', v)}
          fields={[
            { key: 'title', label: 'Title', placeholder: 'Headline', full: true },
            { key: 'date', label: 'Date (YYYY-MM-DD)', placeholder: '2026-05-20' },
            { key: 'source', label: 'Source', placeholder: 'The Economic Times' },
            { key: 'url', label: 'Link', placeholder: 'https://…', full: true },
          ]}
          template={{ date: '', title: '', source: '', url: '' }}
          addLabel="Add release"
        />
      </SectionCard>
      <SectionCard title="Media contact & kit" defaultOpen={false}>
        <div className="grid gap-4">
          <Input
            label="Heading"
            value={page.contact.heading}
            onChange={(e) => set('contact', { ...page.contact, heading: e.target.value })}
          />
          <Input
            label="Email"
            value={page.contact.email}
            onChange={(e) => set('contact', { ...page.contact, email: e.target.value })}
          />
          <Input
            label="Phone"
            value={page.contact.phone}
            onChange={(e) => set('contact', { ...page.contact, phone: e.target.value })}
          />
          <Input
            label="Media kit URL"
            value={page.kit_url}
            onChange={(e) => set('kit_url', e.target.value)}
            helper="Leave blank to hide the media-kit card."
          />
        </div>
      </SectionCard>
    </div>
  );
}

function CorporateEditor({ page, set }) {
  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="Header">
        <EnabledToggle enabled={page.enabled} onChange={(v) => set('enabled', v)} />
        <div className="mt-4">
          <HeroEditor hero={page.hero} onChange={(v) => set('hero', v)} />
        </div>
      </SectionCard>
      <SectionCard title="Content sections" description="Heading + body blocks.">
        <ObjectList
          items={page.sections}
          onChange={(v) => set('sections', v)}
          fields={[
            { key: 'heading', label: 'Heading', placeholder: 'Company overview', full: true },
            { key: 'body', label: 'Body', type: 'textarea', rows: 4 },
          ]}
          template={{ heading: '', body: '' }}
          addLabel="Add section"
        />
      </SectionCard>
      <SectionCard title="Leadership" defaultOpen={false}>
        <ObjectList
          items={page.leadership}
          onChange={(v) => set('leadership', v)}
          fields={[
            { key: 'name', label: 'Name', placeholder: 'A. Sharma' },
            { key: 'title', label: 'Title', placeholder: 'Chief Executive Officer' },
            { key: 'image', label: 'Photo URL', placeholder: 'https://…', full: true },
          ]}
          template={{ name: '', title: '', image: '' }}
          addLabel="Add leader"
        />
      </SectionCard>
      <SectionCard title="Registered entity" defaultOpen={false}>
        <div className="flex flex-col gap-4">
          <Input
            label="Legal name"
            value={page.entity.name}
            onChange={(e) => set('entity', { ...page.entity, name: e.target.value })}
          />
          <Input
            label="CIN"
            value={page.entity.cin}
            onChange={(e) => set('entity', { ...page.entity, cin: e.target.value })}
          />
          <div>
            <p className="mb-2 text-sm font-medium text-ink-secondary">Registered office lines</p>
            <StringList
              items={page.entity.address_lines}
              onChange={(v) => set('entity', { ...page.entity, address_lines: v })}
              placeholder="Address line"
              addLabel="Add line"
            />
          </div>
          <Input
            label="Email"
            value={page.entity.email}
            onChange={(e) => set('entity', { ...page.entity, email: e.target.value })}
          />
          <Input
            label="Phone"
            value={page.entity.phone}
            onChange={(e) => set('entity', { ...page.entity, phone: e.target.value })}
          />
        </div>
      </SectionCard>
      <SectionCard title="Documents" description="Downloadable / linked documents." defaultOpen={false}>
        <ObjectList
          items={page.downloads}
          onChange={(v) => set('downloads', v)}
          fields={[
            { key: 'label', label: 'Label', placeholder: 'Terms of Use' },
            { key: 'url', label: 'URL (/internal or https:)', placeholder: '/terms' },
          ]}
          template={{ label: '', url: '' }}
          addLabel="Add document"
        />
      </SectionCard>
    </div>
  );
}

/**
 * Shared editor for the legal / customer-care policy pages (Privacy, Terms,
 * Refund/Cancellation, Shipping) — a header, a "last updated" label, and a
 * list of heading + body sections.
 */
function PolicyEditor({ page, set }) {
  return (
    <div className="flex flex-col gap-4">
      <SectionCard title="Header" description="Eyebrow, title, subtitle and status.">
        <EnabledToggle enabled={page.enabled} onChange={(v) => set('enabled', v)} />
        <div className="mt-4">
          <HeroEditor hero={page.hero} onChange={(v) => set('hero', v)} />
        </div>
        <div className="mt-4">
          <Input
            label="Last updated label"
            value={page.updated ?? ''}
            onChange={(e) => set('updated', e.target.value)}
            placeholder="Last updated: 11 July 2026"
            helper="Optional line shown under the title. Leave blank to hide."
          />
        </div>
      </SectionCard>
      <SectionCard title="Sections" description="Heading + body blocks shown down the page.">
        <ObjectList
          items={page.sections}
          onChange={(v) => set('sections', v)}
          fields={[
            { key: 'heading', label: 'Heading', placeholder: 'Information we collect', full: true },
            { key: 'body', label: 'Body', type: 'textarea', rows: 4 },
          ]}
          template={{ heading: '', body: '' }}
          addLabel="Add section"
        />
      </SectionCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page tabs
// ---------------------------------------------------------------------------

const TABS = [
  { key: 'about', label: 'About Us', path: '/about', Editor: AboutEditor },
  { key: 'contact', label: 'Contact Us', path: '/contact', Editor: ContactEditor },
  { key: 'careers', label: 'Careers', path: '/careers', Editor: CareersEditor },
  { key: 'stories', label: 'Wellvia Stories', path: '/stories', Editor: StoriesEditor },
  { key: 'press', label: 'Press', path: '/press', Editor: PressEditor },
  { key: 'corporate', label: 'Corporate Information', path: '/corporate', Editor: CorporateEditor },
  { key: 'privacy', label: 'Privacy Policy', path: '/privacy', Editor: PolicyEditor },
  { key: 'terms', label: 'Terms & Conditions', path: '/terms', Editor: PolicyEditor },
  { key: 'refund', label: 'Refund & Cancellation', path: '/refund', Editor: PolicyEditor },
  { key: 'shipping', label: 'Shipping Policy', path: '/shipping', Editor: PolicyEditor },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AdminPagesPage() {
  const { data, isLoading, isError, refetch } = useSitePages();
  const update = useUpdateSitePages();

  const [draft, setDraft] = useState(null);
  const [active, setActive] = useState('about');
  const [saveError, setSaveError] = useState(null);
  const [savedAt, setSavedAt] = useState(false);

  useEffect(() => {
    if (draft !== null) return; // don't clobber edits on background refetch
    const source = data ?? SITE_PAGES_DEFAULTS;
    setDraft(JSON.parse(JSON.stringify(source)));
  }, [data, draft]);

  function setField(pageKey, key, value) {
    setDraft((d) => ({ ...d, [pageKey]: { ...d[pageKey], [key]: value } }));
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
          'Could not save pages. Please try again.',
      );
    }
  }

  if (isLoading && !draft) {
    return (
      <AdminPage title="Company Pages" description="Loading…">
        <div className="flex max-w-3xl flex-col gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      </AdminPage>
    );
  }

  if (isError && !draft) {
    return (
      <AdminPage title="Company Pages" description="Storefront company pages.">
        <div className="flex items-start gap-3 rounded-lg border border-danger/30 bg-danger/8 px-4 py-3 text-sm text-danger">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <p className="font-medium">Could not load company pages</p>
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

  const activeTab = TABS.find((t) => t.key === active) || TABS[0];
  const ActiveEditor = activeTab.Editor;

  return (
    <AdminPage
      title="Company Pages"
      description="Manage the About, Contact, Careers, Stories, Press, Corporate and policy pages (Privacy, Terms, Refund & Shipping) linked from the footer. Changes go live immediately after saving."
    >
      {/* Sticky save toolbar */}
      <div className="sticky top-0 z-10 -mx-6 mb-6 flex items-center justify-between gap-4 border-b border-line-subtle bg-bg-elevated/95 px-6 py-3 backdrop-blur">
        <div className="flex items-center gap-2 min-w-0">
          <FileText className="size-4 shrink-0 text-ink-tertiary" aria-hidden="true" />
          <p className="truncate text-sm text-ink-secondary">
            Editing{' '}
            <span className="font-medium text-ink-primary">{activeTab.label}</span>
          </p>
          <a
            href={activeTab.path}
            target="_blank"
            rel="noreferrer"
            className="hidden shrink-0 items-center gap-1 text-xs text-accent hover:underline sm:flex focus-visible:focus-ring"
            aria-label={`View ${activeTab.label} live`}
          >
            <Globe className="size-3" aria-hidden="true" />
            view live
          </a>
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

      {/* Page switcher pills */}
      <div className="mb-6 flex flex-wrap gap-2">
        {TABS.map((t) => {
          const isActive = t.key === active;
          const published = draft[t.key]?.enabled !== false;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setActive(t.key)}
              className={cn(
                'inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors focus-visible:focus-ring',
                isActive
                  ? 'border-accent bg-accent/12 text-accent shadow-glow-sm'
                  : 'border-line-subtle text-ink-secondary hover:border-line-strong hover:text-ink-primary',
              )}
            >
              {t.label}
              {!published && (
                <Badge tone="neutral" size="sm">Off</Badge>
              )}
            </button>
          );
        })}
      </div>

      {/* Editor panel */}
      <AnimatePresence mode="wait">
        <motion.div
          key={active}
          variants={fadeUp}
          initial="hidden"
          animate="show"
          exit="hidden"
          className="max-w-3xl"
        >
          <ActiveEditor
            page={draft[active]}
            set={(key, value) => setField(active, key, value)}
          />
        </motion.div>
      </AnimatePresence>

      {/* Bottom save strip */}
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
