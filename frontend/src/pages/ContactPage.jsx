import { useState } from 'react';
import { motion } from 'framer-motion';
import { Check } from 'lucide-react';
import { safeUrl } from '@/lib/safeUrl.js';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS, resolvePageIcon } from '@/features/site-pages/defaults.js';
import {
  ContentPage,
  WSection,
  WSectionLabel,
} from '@/components/storefront/ContentPage.jsx';
import { staggerContainer, fadeUp } from '@/lib/motion.js';

// ── Inline form field helpers ────────────────────────────────────────────────

const fieldCls =
  'w-full rounded-xl border border-wline bg-wpaper px-4 py-2.5 text-sm text-wink placeholder:text-wmuted outline-none transition-colors focus:border-wgold focus:ring-1 focus:ring-wgold/30';

function WField({ label, error, children }) {
  return (
    <div className="mb-4">
      <label className="mb-1.5 block text-sm font-medium text-wink">{label}</label>
      {children}
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  );
}

// ── Contact form (logic identical to original) ───────────────────────────────

function ContactForm({ form }) {
  const [values, setValues] = useState({ name: '', email: '', message: '' });
  const [errors, setErrors] = useState({});
  const [submitted, setSubmitted] = useState(false);

  function set(field, val) {
    setValues((v) => ({ ...v, [field]: val }));
    if (errors[field]) setErrors((e) => ({ ...e, [field]: undefined }));
  }

  function onSubmit(e) {
    e.preventDefault();
    const next = {};
    if (!values.name.trim()) next.name = 'Please tell us your name.';
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email.trim()))
      next.email = 'Please enter a valid email address.';
    if (values.message.trim().length < 10)
      next.message = 'Please add a little more detail (10+ characters).';
    if (Object.keys(next).length) {
      setErrors(next);
      return;
    }
    // No backend endpoint for general enquiries — acknowledge locally.
    setSubmitted(true);
    setValues({ name: '', email: '', message: '' });
    window.setTimeout(() => setSubmitted(false), 5000);
  }

  return (
    <div className="rounded-xl2 border border-wline bg-wcard p-6 shadow-sm sm:p-8">
      <h3 className="font-wserif text-xl text-wink">
        {form?.heading || 'Send us a message'}
      </h3>
      {form?.note && (
        <p className="mt-2 text-sm text-wmuted">{form.note}</p>
      )}

      <form onSubmit={onSubmit} noValidate className="mt-6">
        <div className="grid gap-x-4 sm:grid-cols-2">
          <WField label="Your name" error={errors.name}>
            <input
              className={fieldCls}
              value={values.name}
              onChange={(e) => set('name', e.target.value)}
              autoComplete="name"
              required
            />
          </WField>
          <WField label="Email" error={errors.email}>
            <input
              type="email"
              className={fieldCls}
              value={values.email}
              onChange={(e) => set('email', e.target.value)}
              autoComplete="email"
              required
            />
          </WField>
        </div>
        <WField label="Message" error={errors.message}>
          <textarea
            className={`${fieldCls} resize-none`}
            rows={5}
            value={values.message}
            onChange={(e) => set('message', e.target.value)}
            placeholder="How can we help?"
            required
          />
        </WField>
        <div className="flex items-center gap-4">
          <button
            type="submit"
            disabled={submitted}
            className="rounded-full bg-wgreen px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-wgreen-dark disabled:opacity-60"
          >
            Send message
          </button>
          {submitted && (
            <motion.span
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex items-center gap-1.5 text-sm text-green-700"
              role="status"
            >
              <Check className="size-4" aria-hidden="true" />
              {form?.success || "Thanks — we'll be in touch shortly."}
            </motion.span>
          )}
        </div>
      </form>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function ContactPage() {
  // ── Data wiring (unchanged) ──────────────────────────────────────────────
  const { data, isLoading } = useSitePages();

  // Show skeleton while the first fetch is in-flight (no cached data yet).
  if (isLoading && !data) {
    return (
      <ContentPage isLoading>
        <div className="animate-pulse space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-28 rounded-xl2 bg-wline/40" />
            ))}
          </div>
          <div className="mt-6 h-64 rounded-xl2 bg-wline/40" />
        </div>
      </ContentPage>
    );
  }

  const page = { ...SITE_PAGES_DEFAULTS.contact, ...data?.contact };

  if (page.enabled === false) {
    return <ContentPage disabled disabledTitle="Contact Us" />;
  }

  return (
    <ContentPage
      eyebrow={page.hero?.eyebrow}
      title={page.hero?.title}
      subtitle={page.hero?.subtitle}
    >
      {/* ── Contact method tiles ──────────────────────────────────────── */}
      {page.methods?.length > 0 && (
        <WSection className="mt-0">
          <motion.div
            variants={staggerContainer(0.06)}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, margin: '-60px' }}
            className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
          >
            {page.methods.map((m, i) => {
              const Icon = resolvePageIcon(m.icon);
              const body = (
                <div className="flex flex-col gap-3 p-5">
                  <span className="grid size-10 place-items-center rounded-full bg-wgold/10 text-wgold">
                    <Icon className="size-5" aria-hidden="true" />
                  </span>
                  <div>
                    <h3 className="font-semibold text-wink">{m.title}</h3>
                    <p className="mt-0.5 text-sm text-wmuted">{m.detail}</p>
                  </div>
                </div>
              );
              return (
                <motion.div key={i} variants={fadeUp}>
                  <div className="h-full rounded-xl2 border border-wline bg-wcard shadow-sm transition-shadow hover:shadow-md">
                    {m.href ? (
                      <a
                        href={safeUrl(m.href)}
                        className="block h-full rounded-xl2 outline-none focus-visible:ring-2 focus-visible:ring-wgold/50"
                      >
                        {body}
                      </a>
                    ) : (
                      body
                    )}
                  </div>
                </motion.div>
              );
            })}
          </motion.div>
        </WSection>
      )}

      {/* ── Form + offices ────────────────────────────────────────────── */}
      <WSection className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div>
          {page.intro && (
            <p className="mb-5 text-sm leading-relaxed text-wmuted">{page.intro}</p>
          )}
          <ContactForm form={page.form} />
        </div>

        {/* Offices */}
        {page.offices?.length > 0 && (
          <div>
            <WSectionLabel className="mb-4">Our offices</WSectionLabel>
            <motion.div
              variants={staggerContainer(0.07)}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, margin: '-60px' }}
              className="flex flex-col gap-3"
            >
              {page.offices.map((o, i) => (
                <motion.div key={i} variants={fadeUp}>
                  <div className="rounded-xl2 border border-wline bg-wcard p-5 shadow-sm">
                    <h3 className="font-semibold text-wink">{o.city}</h3>
                    <address className="mt-1.5 not-italic text-sm leading-6 text-wmuted">
                      {(o.lines || []).map((line, li) => (
                        <span key={li} className="block">
                          {line}
                        </span>
                      ))}
                    </address>
                  </div>
                </motion.div>
              ))}
            </motion.div>
          </div>
        )}
      </WSection>
    </ContentPage>
  );
}
