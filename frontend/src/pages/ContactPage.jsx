import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Check, MapPin, Mail, Phone } from 'lucide-react';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import { useSubmitContactMessage } from '@/features/contact/hooks.js';

// ── Inline form field helpers ────────────────────────────────────────────────

const fieldCls =
  'w-full rounded-xl border border-wline bg-wpaper px-4 py-2.5 text-sm text-wink placeholder:text-wmuted outline-none transition-colors focus:border-wgold focus:ring-1 focus:ring-wgold/30';

function WField({ label, error, optional, children }) {
  return (
    <div className="mb-4">
      <label className="mb-1.5 block text-sm font-medium text-wink">
        {label}
        {optional && (
          <span className="ml-1.5 text-xs font-normal text-wmuted">(optional)</span>
        )}
      </label>
      {children}
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  );
}

// ── Contact form — wired to POST /contact ───────────────────────────────────

const EMPTY = { name: '', email: '', phone: '', subject: '', message: '' };

function ContactForm({ form }) {
  const [values, setValues] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const submit = useSubmitContactMessage();

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

    const payload = {
      name: values.name.trim(),
      email: values.email.trim(),
      message: values.message.trim(),
    };
    if (values.phone.trim()) payload.phone = values.phone.trim();
    if (values.subject.trim()) payload.subject = values.subject.trim();

    submit.mutate(payload, {
      onSuccess: () => setValues(EMPTY),
    });
  }

  const errorText =
    submit.error?.response?.status === 429
      ? 'Too many messages from this connection — please try again in a little while.'
      : "Couldn't send your message. Please try again.";

  return (
    <div className="rounded-xl2 border border-wline bg-wcard p-6 shadow-sm sm:p-8">
      <h3 className="font-wserif text-xl text-wink">
        {form?.heading || 'Send us a message'}
      </h3>
      {form?.note && <p className="mt-2 text-sm text-wmuted">{form.note}</p>}

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
          <WField label="Phone" optional>
            <input
              type="tel"
              className={fieldCls}
              value={values.phone}
              onChange={(e) => set('phone', e.target.value)}
              autoComplete="tel"
            />
          </WField>
          <WField label="Subject" optional>
            <input
              className={fieldCls}
              value={values.subject}
              onChange={(e) => set('subject', e.target.value)}
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
        <div className="flex flex-wrap items-center gap-4">
          <button
            type="submit"
            disabled={submit.isPending}
            className="rounded-full bg-wgreen px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-wgreen-dark disabled:opacity-60"
          >
            {submit.isPending ? 'Sending…' : 'Send message'}
          </button>
          {submit.isSuccess && (
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
          {submit.isError && (
            <span className="text-sm text-red-600" role="alert">
              {errorText}
            </span>
          )}
        </div>
      </form>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function ContactPage() {
  // Site-pages config drives the enabled flag, info-card details and form copy.
  const { data, isLoading } = useSitePages();

  if (isLoading && !data) {
    return (
      <div className="min-h-[70vh] px-5 sm:px-10 lg:px-14 py-10">
        <div className="max-w-[1050px] mx-auto animate-pulse space-y-6">
          <div className="h-10 w-64 rounded bg-wline/50" />
          <div className="grid md:grid-cols-[1fr_1.3fr] gap-10">
            <div className="h-[260px] rounded-xl2 bg-wline/40" />
            <div className="h-[420px] rounded-xl2 bg-wline/40" />
          </div>
        </div>
      </div>
    );
  }

  const page = { ...SITE_PAGES_DEFAULTS.contact, ...data?.contact };

  if (page.enabled === false) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center px-6 py-20">
        <div className="text-center">
          <p className="font-wserif text-4xl leading-tight text-wink">Get in Touch</p>
          <p className="mt-4 text-base text-wmuted">
            This page isn&apos;t available right now. Please check back soon.
          </p>
        </div>
      </div>
    );
  }

  // Contact details from the admin-managed contact methods (with fallbacks).
  const methods = page.methods ?? [];
  const findDetail = (...keys) =>
    methods.find((m) =>
      keys.some(
        (k) =>
          (m.icon || '').toLowerCase().includes(k) ||
          (m.title || '').toLowerCase().includes(k),
      ),
    )?.detail;

  const address = findDetail('mappin', 'visit', 'address') || 'Bengaluru, Karnataka, India';
  const email = findDetail('mail', 'email') || 'care@shopwellvia.in';
  const phone = findDetail('phone', 'call') || '+91 90000 00000';

  const contactRows = [
    { Icon: MapPin, title: 'Visit us', detail: address },
    { Icon: Mail, title: 'Email us', detail: `${email} — replies within 24 hours` },
    { Icon: Phone, title: 'Call us', detail: `${phone} · Mon–Sat, 9am–6pm IST` },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="pb-16"
    >
      <div className="max-w-[1050px] mx-auto px-5 sm:px-10 lg:px-14 pt-10 lg:pt-14">
        {/* Heading */}
        <h1 className="font-wserif font-semibold text-[clamp(30px,4vw,42px)] text-wink m-0 mb-1.5">
          Get in Touch
        </h1>
        <p className="font-wserif text-[clamp(16px,1.7vw,20px)] text-wmuted m-0 mb-9 lg:mb-11">
          {page.intro ||
            "Questions about a ritual, an order, or anything wellness? We're listening."}
        </p>

        <div className="grid md:grid-cols-[1fr_1.3fr] gap-8 lg:gap-12 items-start">
          {/* Left: contact details + track-order nudge */}
          <div className="flex flex-col gap-6">
            {contactRows.map(({ Icon, title, detail }) => (
              <div key={title} className="flex items-start gap-3.5">
                <span className="w-11 h-11 shrink-0 rounded-full border border-wgreen/70 flex items-center justify-center text-wgreen">
                  <Icon className="size-[19px]" strokeWidth={1.6} aria-hidden="true" />
                </span>
                <div>
                  <div className="font-wserif text-[19px] text-wink">{title}</div>
                  <div className="text-[14px] text-wmuted mt-0.5 leading-relaxed">
                    {detail}
                  </div>
                </div>
              </div>
            ))}

            {/* Sage track-order box */}
            <div className="bg-wsage rounded-xl2 px-5 py-4 mt-1">
              <div className="font-wserif text-[18px] text-[#16301f]">
                Track an order instead?
              </div>
              <Link
                to="/orders"
                className="mt-1 inline-block text-[14px] text-wgreen underline underline-offset-2 hover:text-wgreen-dark transition-colors"
              >
                Go to Track Order →
              </Link>
            </div>
          </div>

          {/* Right: message form — real POST /contact */}
          <ContactForm form={page.form} />
        </div>
      </div>
    </motion.div>
  );
}
