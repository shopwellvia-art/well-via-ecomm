import { Plus, Trash2, Sparkles, TicketPercent, ListChecks, HelpCircle, Clock3 } from 'lucide-react';
import { Card } from '@/components/ui/Card.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { Select } from '@/components/ui/Select.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { FLAVOURS } from '@/lib/catalogOptions.js';

/**
 * Admin editor for the storefront merchandising + rich PDP content fields.
 *
 * Controlled: `content` holds { flavour, is_combo, badge, offer_text,
 * coupon_code, coupon_hint, short_description, ingredients, highlights,
 * benefits, usage_steps, faqs } and `onChange(patch)` merges updates into the
 * parent form state. Every field is optional — the storefront hides a PDP
 * section when its data is absent.
 */
export function ProductContentEditor({ content, onChange }) {
  const set = (key) => (e) => onChange({ [key]: e.target.value });

  /* ── generic row-list helpers (benefits / usage_steps / faqs) ── */
  const rows = (key) => (Array.isArray(content[key]) ? content[key] : []);
  const setRow = (key, idx, field) => (e) => {
    const next = rows(key).map((r, i) => (i === idx ? { ...r, [field]: e.target.value } : r));
    onChange({ [key]: next });
  };
  const addRow = (key, empty) => () => onChange({ [key]: [...rows(key), empty] });
  const removeRow = (key, idx) => () =>
    onChange({ [key]: rows(key).filter((_, i) => i !== idx) });

  /* highlights is a plain string list */
  const highlights = Array.isArray(content.highlights) ? content.highlights : [];

  return (
    <div className="space-y-5">
      {/* ── Merchandising ── */}
      <Card className="p-5 shadow-md">
        <p className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
          <Sparkles className="size-3.5 shrink-0" aria-hidden="true" />
          Storefront merchandising
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Flavour"
            value={content.flavour ?? ''}
            onChange={set('flavour')}
            helper="Shown as a tag on cards/PDP; drives the flavour filter."
          >
            <option value="">— None —</option>
            {FLAVOURS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </Select>
          <Input
            label="Card badge"
            value={content.badge ?? ''}
            onChange={set('badge')}
            helper="Optional ribbon text (e.g. Bestseller). Overrides derived badges."
            placeholder="Bestseller"
          />
        </div>
        <label className="mt-4 flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            checked={!!content.is_combo}
            onChange={(e) => onChange({ is_combo: e.target.checked })}
            className="mt-0.5 size-4 rounded-sm border border-line-subtle bg-bg-elevated text-accent focus-visible:focus-ring"
          />
          <span className="flex-1 text-sm">
            <span className="block font-medium text-ink-primary">Combo pack</span>
            <span className="block text-xs text-ink-tertiary">
              Shows under Categories → Combo Packs and the “Combo Packs” offer filter.
            </span>
          </span>
        </label>
        <div className="mt-4">
          <Textarea
            label="Short description"
            value={content.short_description ?? ''}
            onChange={set('short_description')}
            helper="One-liner under the product name on cards and wishlist rows."
            maxRows={3}
          />
        </div>
      </Card>

      {/* ── Offer & coupon ── */}
      <Card className="p-5 shadow-md">
        <p className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
          <TicketPercent className="size-3.5 shrink-0" aria-hidden="true" />
          Offer &amp; coupon (PDP)
        </p>
        <div className="grid gap-4 sm:grid-cols-3">
          <Input
            label="Offer chip"
            value={content.offer_text ?? ''}
            onChange={set('offer_text')}
            placeholder="Buy 1 Get 1 free!"
          />
          <Input
            label="Coupon code"
            value={content.coupon_code ?? ''}
            onChange={set('coupon_code')}
            helper="Must exist in Admin → Coupons to actually apply."
            placeholder="WELLVIA"
          />
          <Input
            label="Coupon hint"
            value={content.coupon_hint ?? ''}
            onChange={set('coupon_hint')}
            placeholder="Get it for ₹800"
          />
        </div>
      </Card>

      {/* ── Pack highlights ── */}
      <Card className="p-5 shadow-md">
        <p className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
          <ListChecks className="size-3.5 shrink-0" aria-hidden="true" />
          Pack highlights
        </p>
        <p className="mb-4 text-xs text-ink-tertiary">
          Chips shown under the PDP benefits — e.g. “30 Gummies”, “15 Servings”, “Berry Flavour”.
        </p>
        <div className="space-y-2">
          {highlights.map((h, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <Input
                value={typeof h === 'string' ? h : (h?.label ?? '')}
                onChange={(e) => {
                  const next = highlights.map((x, i) => (i === idx ? e.target.value : x));
                  onChange({ highlights: next });
                }}
                placeholder="30 Gummies"
                className="flex-1"
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onChange({ highlights: highlights.filter((_, i) => i !== idx) })}
                aria-label="Remove highlight"
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onChange({ highlights: [...highlights, ''] })}
          >
            <Plus className="size-4" aria-hidden="true" /> Add highlight
          </Button>
        </div>
        <div className="mt-4">
          <Textarea
            label="Ingredients"
            value={content.ingredients ?? ''}
            onChange={set('ingredients')}
            helper={'One per line: "Melatonin — Regulates sleep cycle". Shown in “Clean & Effective Ingredients”.'}
            maxRows={6}
          />
        </div>
      </Card>

      {/* ── Benefits ── */}
      <Card className="p-5 shadow-md">
        <p className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
          <Sparkles className="size-3.5 shrink-0" aria-hidden="true" />
          Benefits — “Why You’ll Love It”
        </p>
        <p className="mb-4 text-xs text-ink-tertiary">
          Up to 4 icon cards on the PDP. Icon is a keyword (sleep, calm, energy, glow, immunity, gut…).
        </p>
        <div className="space-y-3">
          {rows('benefits').map((b, idx) => (
            <div key={idx} className="grid gap-2 sm:grid-cols-[110px_1fr_1.5fr_auto]">
              <Input value={b.icon ?? ''} onChange={setRow('benefits', idx, 'icon')} placeholder="sleep" aria-label="Icon" />
              <Input value={b.title ?? ''} onChange={setRow('benefits', idx, 'title')} placeholder="Fall Asleep Faster" aria-label="Title" />
              <Input value={b.text ?? ''} onChange={setRow('benefits', idx, 'text')} placeholder="Helps you relax and drift off naturally." aria-label="Text" />
              <Button type="button" variant="ghost" size="sm" onClick={removeRow('benefits', idx)} aria-label="Remove benefit">
                <Trash2 className="size-4" aria-hidden="true" />
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" onClick={addRow('benefits', { icon: '', title: '', text: '' })}>
            <Plus className="size-4" aria-hidden="true" /> Add benefit
          </Button>
        </div>
      </Card>

      {/* ── What to expect (usage steps) ── */}
      <Card className="p-5 shadow-md">
        <p className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
          <Clock3 className="size-3.5 shrink-0" aria-hidden="true" />
          What to expect — timeline
        </p>
        <p className="mb-4 text-xs text-ink-tertiary">
          Ordered steps, e.g. “30 Mins Before Bed → Take 2 Sleep Gummies”. Also used for “How to use”.
        </p>
        <div className="space-y-3">
          {rows('usage_steps').map((sStep, idx) => (
            <div key={idx} className="grid gap-2 sm:grid-cols-[1fr_1.5fr_auto]">
              <Input value={sStep.label ?? ''} onChange={setRow('usage_steps', idx, 'label')} placeholder="30 Mins Before Bed" aria-label="Step label" />
              <Input value={sStep.text ?? ''} onChange={setRow('usage_steps', idx, 'text')} placeholder="Take 2 Sleep Gummies" aria-label="Step text" />
              <Button type="button" variant="ghost" size="sm" onClick={removeRow('usage_steps', idx)} aria-label="Remove step">
                <Trash2 className="size-4" aria-hidden="true" />
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" onClick={addRow('usage_steps', { label: '', text: '' })}>
            <Plus className="size-4" aria-hidden="true" /> Add step
          </Button>
        </div>
      </Card>

      {/* ── FAQ ── */}
      <Card className="p-5 shadow-md">
        <p className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
          <HelpCircle className="size-3.5 shrink-0" aria-hidden="true" />
          FAQ
        </p>
        <div className="space-y-3">
          {rows('faqs').map((f, idx) => (
            <div key={idx} className="space-y-2 rounded-md border border-line-subtle p-3">
              <div className="flex items-start gap-2">
                <Input
                  value={f.q ?? ''}
                  onChange={setRow('faqs', idx, 'q')}
                  placeholder="How many should I take?"
                  aria-label="Question"
                  className="flex-1"
                />
                <Button type="button" variant="ghost" size="sm" onClick={removeRow('faqs', idx)} aria-label="Remove FAQ">
                  <Trash2 className="size-4" aria-hidden="true" />
                </Button>
              </div>
              <Textarea
                value={f.a ?? ''}
                onChange={setRow('faqs', idx, 'a')}
                placeholder="Take 2 gummies daily, 30 minutes before bed."
                aria-label="Answer"
                maxRows={4}
              />
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" onClick={addRow('faqs', { q: '', a: '' })}>
            <Plus className="size-4" aria-hidden="true" /> Add FAQ
          </Button>
        </div>
      </Card>
    </div>
  );
}
