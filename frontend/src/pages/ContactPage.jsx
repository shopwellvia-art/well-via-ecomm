import { useState } from 'react';
import { safeUrl } from '@/lib/safeUrl.js';
import { Check } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS, resolvePageIcon } from '@/features/site-pages/defaults.js';
import {
  CompanyHero,
  Section,
  SectionLabel,
  PageDisabled,
} from '@/features/site-pages/components.jsx';

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
    // No backend endpoint for general enquiries — acknowledge locally, the
    // same pattern the footer newsletter uses.
    setSubmitted(true);
    setValues({ name: '', email: '', message: '' });
    window.setTimeout(() => setSubmitted(false), 5000);
  }

  return (
    <Card>
      <CardBody className="p-6 sm:p-8">
        <h3 className="text-h3 text-ink-primary">{form?.heading || 'Send us a message'}</h3>
        {form?.note && <p className="mt-1 text-sm text-ink-secondary">{form.note}</p>}

        <form onSubmit={onSubmit} noValidate className="mt-5">
          <div className="grid gap-x-4 sm:grid-cols-2">
            <Input
              label="Your name"
              value={values.name}
              onChange={(e) => set('name', e.target.value)}
              error={errors.name}
              autoComplete="name"
            />
            <Input
              label="Email"
              type="email"
              value={values.email}
              onChange={(e) => set('email', e.target.value)}
              error={errors.email}
              autoComplete="email"
            />
          </div>
          <Textarea
            label="Message"
            rows={5}
            value={values.message}
            onChange={(e) => set('message', e.target.value)}
            error={errors.message}
            placeholder="How can we help?"
          />
          <div className="mt-2 flex items-center gap-4">
            <Button type="submit">Send message</Button>
            {submitted && (
              <span className="flex items-center gap-1.5 text-sm text-success" role="status">
                <Check className="size-4" aria-hidden="true" />
                {form?.success || 'Thanks — we’ll be in touch shortly.'}
              </span>
            )}
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

export default function ContactPage() {
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.contact, ...data?.contact };

  if (page.enabled === false) {
    return (
      <Page>
        <PageDisabled title="Contact Us" />
      </Page>
    );
  }

  return (
    <Page>
      <CompanyHero hero={page.hero} current="Contact Us" />

      {/* Contact methods */}
      {page.methods?.length > 0 && (
        <Section className="mt-12">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {page.methods.map((m, i) => {
              const Icon = resolvePageIcon(m.icon);
              const body = (
                <CardBody className="flex flex-col gap-3">
                  <span className="grid size-11 place-items-center rounded-xl bg-accent/12 text-accent">
                    <Icon className="size-5" aria-hidden="true" />
                  </span>
                  <div>
                    <h3 className="font-semibold text-ink-primary">{m.title}</h3>
                    <p className="mt-0.5 text-sm text-ink-secondary">{m.detail}</p>
                  </div>
                </CardBody>
              );
              return (
                <Card
                  key={i}
                  className="transition-colors hover:border-line-strong"
                >
                  {m.href ? (
                    <a href={safeUrl(m.href)} className="block rounded-lg focus-visible:focus-ring">
                      {body}
                    </a>
                  ) : (
                    body
                  )}
                </Card>
              );
            })}
          </div>
        </Section>
      )}

      <Section className="grid gap-8 lg:grid-cols-[1.4fr_1fr]">
        <div>
          {page.intro && <p className="mb-5 max-w-xl text-ink-secondary">{page.intro}</p>}
          <ContactForm form={page.form} />
        </div>

        {/* Offices */}
        {page.offices?.length > 0 && (
          <div>
            <SectionLabel className="text-h3">Our offices</SectionLabel>
            <div className="mt-5 flex flex-col gap-4">
              {page.offices.map((o, i) => (
                <Card key={i}>
                  <CardBody>
                    <h3 className="font-semibold text-ink-primary">{o.city}</h3>
                    <address className="mt-1.5 not-italic text-sm leading-6 text-ink-secondary">
                      {(o.lines || []).map((line, li) => (
                        <span key={li} className="block">
                          {line}
                        </span>
                      ))}
                    </address>
                  </CardBody>
                </Card>
              ))}
            </div>
          </div>
        )}
      </Section>
    </Page>
  );
}
