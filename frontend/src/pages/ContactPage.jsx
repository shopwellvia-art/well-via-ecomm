import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { MapPin, Mail, Phone, CheckCircle2 } from 'lucide-react';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import { useSubmitContactMessage } from '@/features/contact/hooks.js';
import { toast } from '@/components/ui/Toaster.jsx';
import { cn } from '@/lib/utils.js';

// ── Message form ─────────────────────────────────────────────────────────────

// Shared input style — wellness skin, matches LoginPage (no @/components/ui dependency).
const inputCls =
  'w-full bg-wcard border border-wline rounded-xl px-[17px] py-[15px] text-[14px] text-wink ' +
  'placeholder:text-wmuted outline-none transition-colors focus:border-wgreen font-wsans ' +
  'disabled:opacity-60 disabled:pointer-events-none';

const labelCls =
  'text-[11px] tracking-[0.18em] uppercase text-wmuted font-medium block mb-1.5';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const EMPTY_FORM = { name: '', email: '', phone: '', subject: '', message: '' };

// Mirrors the backend ContactMessageCreate schema:
// name 1–120 (required), email (required), phone ≤20 (optional),
// subject ≤200 (optional), message 10–5000 (required).
function validateForm(form) {
  const errors = {};
  const name = form.name.trim();
  if (!name) errors.name = 'Please enter your name.';
  else if (name.length > 120) errors.name = 'Name must be 120 characters or fewer.';

  const email = form.email.trim();
  if (!email) errors.email = 'Please enter your email address.';
  else if (!EMAIL_RE.test(email)) errors.email = 'Please enter a valid email address.';

  const phone = form.phone.trim();
  if (phone && phone.length > 20) errors.phone = 'Phone must be 20 characters or fewer.';
  else if (phone && !/^[+()\-\s\d]+$/.test(phone))
    errors.phone = 'Please enter a valid phone number.';

  if (form.subject.trim().length > 200)
    errors.subject = 'Subject must be 200 characters or fewer.';

  const message = form.message.trim();
  if (!message) errors.message = 'Please write a message.';
  else if (message.length < 10) errors.message = 'Message must be at least 10 characters.';
  else if (message.length > 5000)
    errors.message = 'Message must be 5000 characters or fewer.';

  return errors;
}

function FieldError({ id, children }) {
  if (!children) return null;
  return (
    <p id={id} role="alert" className="mt-1.5 mb-0 text-xs text-red-600">
      {children}
    </p>
  );
}

function ContactForm({ copy }) {
  const submitMessage = useSubmitContactMessage();
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [submitted, setSubmitted] = useState(false);

  const busy = submitMessage.isPending;

  const set = (key) => (e) => {
    const value = e.target.value;
    setForm((f) => ({ ...f, [key]: value }));
    // Clear the field's error as soon as the visitor starts fixing it.
    setErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
  };

  function handleSubmit(e) {
    e.preventDefault();
    if (busy) return;

    const nextErrors = validateForm(form);
    if (Object.values(nextErrors).some(Boolean)) {
      setErrors(nextErrors);
      return;
    }

    submitMessage.mutate(
      {
        name: form.name.trim(),
        email: form.email.trim(),
        phone: form.phone.trim() || undefined,
        subject: form.subject.trim() || undefined,
        message: form.message.trim(),
      },
      {
        onSuccess: () => setSubmitted(true),
        onError: (err) =>
          toast.error(
            err?.response?.data?.error?.message ||
              'Could not send your message. Please try again.',
          ),
      },
    );
  }

  function handleReset() {
    submitMessage.reset();
    setForm(EMPTY_FORM);
    setErrors({});
    setSubmitted(false);
  }

  // Success state replaces the form entirely.
  if (submitted) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
        className="rounded-2xl border border-wline bg-wcard px-6 py-10 sm:px-10 text-center"
        role="status"
      >
        <CheckCircle2 className="mx-auto size-10 text-wgreen" strokeWidth={1.5} aria-hidden="true" />
        <p className="mt-4 mb-0 font-wserif text-[20px] text-wink">Message sent</p>
        <p className="mt-2 mb-0 text-[14px] text-wmuted max-w-sm mx-auto">
          {copy.success || "Thanks for reaching out — we'll be in touch shortly."}
        </p>
        <button
          type="button"
          onClick={handleReset}
          className="mt-5 text-[13px] text-wgreen underline underline-offset-2 hover:text-wgreen-dark transition-colors"
        >
          Send another message
        </button>
      </motion.div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      className="rounded-2xl border border-wline bg-wcard px-6 py-6 sm:px-10 sm:py-8 flex flex-col gap-4"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="contact-name" className={labelCls}>
            Name
          </label>
          <input
            id="contact-name"
            value={form.name}
            onChange={set('name')}
            placeholder="Your full name"
            autoComplete="name"
            maxLength={120}
            disabled={busy}
            aria-invalid={errors.name ? 'true' : undefined}
            aria-describedby={errors.name ? 'contact-name-error' : undefined}
            className={cn(inputCls, errors.name && 'border-red-300 focus:border-red-400')}
          />
          <FieldError id="contact-name-error">{errors.name}</FieldError>
        </div>

        <div>
          <label htmlFor="contact-email" className={labelCls}>
            Email
          </label>
          <input
            id="contact-email"
            type="email"
            value={form.email}
            onChange={set('email')}
            placeholder="you@example.com"
            autoComplete="email"
            disabled={busy}
            aria-invalid={errors.email ? 'true' : undefined}
            aria-describedby={errors.email ? 'contact-email-error' : undefined}
            className={cn(inputCls, errors.email && 'border-red-300 focus:border-red-400')}
          />
          <FieldError id="contact-email-error">{errors.email}</FieldError>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="contact-phone" className={labelCls}>
            Phone <span className="normal-case tracking-normal">(optional)</span>
          </label>
          <input
            id="contact-phone"
            type="tel"
            value={form.phone}
            onChange={set('phone')}
            placeholder="+91 90000 00000"
            autoComplete="tel"
            maxLength={20}
            disabled={busy}
            aria-invalid={errors.phone ? 'true' : undefined}
            aria-describedby={errors.phone ? 'contact-phone-error' : undefined}
            className={cn(inputCls, errors.phone && 'border-red-300 focus:border-red-400')}
          />
          <FieldError id="contact-phone-error">{errors.phone}</FieldError>
        </div>

        <div>
          <label htmlFor="contact-subject" className={labelCls}>
            Subject <span className="normal-case tracking-normal">(optional)</span>
          </label>
          <input
            id="contact-subject"
            value={form.subject}
            onChange={set('subject')}
            placeholder="How can we help?"
            maxLength={200}
            disabled={busy}
            aria-invalid={errors.subject ? 'true' : undefined}
            aria-describedby={errors.subject ? 'contact-subject-error' : undefined}
            className={cn(inputCls, errors.subject && 'border-red-300 focus:border-red-400')}
          />
          <FieldError id="contact-subject-error">{errors.subject}</FieldError>
        </div>
      </div>

      <div>
        <label htmlFor="contact-message" className={labelCls}>
          Message
        </label>
        <textarea
          id="contact-message"
          value={form.message}
          onChange={set('message')}
          placeholder="Tell us a little about your query (at least 10 characters)…"
          rows={5}
          maxLength={5000}
          disabled={busy}
          aria-invalid={errors.message ? 'true' : undefined}
          aria-describedby={errors.message ? 'contact-message-error' : undefined}
          className={cn(
            inputCls,
            'resize-y min-h-[120px]',
            errors.message && 'border-red-300 focus:border-red-400',
          )}
        />
        <FieldError id="contact-message-error">{errors.message}</FieldError>
      </div>

      <button
        type="submit"
        disabled={busy}
        aria-busy={busy}
        className="w-full bg-wgreen text-white rounded-full py-4 text-[14.5px] tracking-wide cursor-pointer hover:bg-wgreen-dark transition-colors disabled:opacity-60 disabled:pointer-events-none flex items-center justify-center gap-2"
      >
        {busy ? (
          <svg className="size-4 animate-spin360" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        ) : (
          'Send message'
        )}
      </button>
    </form>
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
          <div className="h-[220px] rounded-b-3xl bg-wline/40" />
          <div className="h-[180px] max-w-[560px] mx-auto rounded-xl2 bg-wline/40" />
          <div className="h-[420px] max-w-[560px] mx-auto rounded-xl2 bg-wline/40" />
        </div>
      </div>
    );
  }

  const page = { ...SITE_PAGES_DEFAULTS.contact, ...data?.contact };

  if (page.enabled === false) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center px-6 py-20">
        <div className="text-center">
          <p className="font-wserif text-4xl leading-tight text-wink">Contact Us</p>
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

  const hours = page.hours || 'Monday – Saturday (9:00 AM – 6:00 PM IST)';
  const responseNote =
    page.responseNote || 'We aim to respond to all queries within 24–48 business hours.';

  // Admin-managed form copy; merged field-by-field so a partial override from
  // the API keeps the remaining defaults.
  const formCopy = { ...SITE_PAGES_DEFAULTS.contact.form, ...page.form };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="pb-16"
    >
      {/* Hero banner */}
     <div className="relative overflow-hidden min-h-[180px] sm:min-h-[300px] px-6 py-10 sm:py-20 flex flex-col justify-center items-center text-center">
  <img
    src="/hero-contact.png"
    alt=""
    className="absolute inset-0 w-full h-full object-cover object-center"
  />

  {/* Optional overlay for better text readability */}
  <div className="absolute inset-0 bg-black/20" />

  {/* <h1 className="relative z-10 font-wserif font-semibold text-[clamp(30px,4vw,42px)] text-white m-0">
    Contact Us
  </h1>

  <p className="relative z-10 mt-3 max-w-md mx-auto font-wserif text-[clamp(15px,1.6vw,18px)] text-white">
    {page.intro ||
      "We're here to help! Reach out for any queries, feedback or support."}
  </p> */}
</div>

      <div className="max-w-[620px] mx-auto px-5 sm:px-10 mt-10 lg:mt-14">
        <p className="text-center font-wserif text-[19px] text-wink mb-6">
          Feel free to reach out to us at any time.
        </p>

        {/* Contact details block */}
        <div className="rounded-2xl border border-wline bg-wcard px-6 py-6 sm:px-10 sm:py-8 flex flex-col gap-4">
          {[
            { label: 'Mail id', value: email, Icon: Mail },
            { label: 'Phone', value: phone, Icon: Phone },
            { label: 'Address', value: address, Icon: MapPin },
          ].map(({ label, value, Icon }) => (
            <div key={label} className="flex items-start gap-3 text-[15px]">
              <Icon className="size-[17px] mt-0.5 text-wgreen shrink-0" strokeWidth={1.6} aria-hidden="true" />
              <p className="m-0 text-wink">
                <span className="font-medium">{label} :</span>{' '}
                <span className="text-wmuted">{value}</span>
              </p>
            </div>
          ))}
        </div>

        {/* Message form */}
        <div className="mt-10">
          <div className="text-center mb-5">
            <p className="font-wserif text-[19px] text-wink m-0">{formCopy.heading}</p>
            {formCopy.note && (
              <p className="text-[13px] text-wmuted mt-1 mb-0">{formCopy.note}</p>
            )}
          </div>
          <ContactForm copy={formCopy} />
        </div>

        {/* Support hours */}
        <div className="text-center mt-10">
          <p className="font-wserif text-[18px] text-wink m-0 mb-1">Customer Support Hours:</p>
          <p className="text-[15px] font-medium text-wink m-0">{hours}</p>
          <p className="text-[13px] text-wmuted mt-2 max-w-sm mx-auto">{responseNote}</p>
        </div>

        {/* Sage track-order box */}
        <div className="bg-wsage rounded-xl2 px-5 py-4 mt-10 mb-4 text-center">
          <div className="font-wserif text-[18px] text-[#16301f]">
            Track an order instead?
          </div>
          <Link
            to="/orders"
            className="mt-1 inline-block text-[14px] text-[#08112C] underline underline-offset-2 hover:text-wgreen-dark transition-colors"
          >
            Go to Track Order →
          </Link>
        </div>
      </div>
    </motion.div>
  );
}