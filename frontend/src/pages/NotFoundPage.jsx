import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Compass, ArrowLeft, Search } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { heroContainer, fadeUp } from '@/lib/motion.js';

export default function NotFoundPage() {
  return (
    <main className="relative mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-content flex-col items-center justify-center px-6 py-12 text-center">
      {/* Ambient glows */}
      <div
        aria-hidden="true"
        className="absolute left-1/2 top-1/3 -z-10 size-[420px] -translate-x-1/2 rounded-full bg-accent/10 blur-[150px]"
      />
      <div
        aria-hidden="true"
        className="absolute right-1/3 bottom-1/3 -z-10 size-[240px] rounded-full bg-accent/6 blur-[100px]"
      />

      <motion.div
        variants={heroContainer}
        initial="hidden"
        animate="show"
        className="flex flex-col items-center"
      >
        {/* Icon */}
        <motion.span
          variants={fadeUp}
          className="grid size-16 place-items-center rounded-2xl border border-line-subtle bg-bg-elevated shadow-md"
        >
          <Compass className="size-8 text-accent" aria-hidden="true" />
        </motion.span>

        {/* Number */}
        <motion.p
          variants={fadeUp}
          className="mt-8 text-display tracking-tight text-ink-primary"
        >
          404
        </motion.p>

        {/* Headline */}
        <motion.h1
          variants={fadeUp}
          className="mt-2 text-h2 tracking-tight text-ink-primary"
        >
          This page wandered off
        </motion.h1>

        {/* Support copy */}
        <motion.p
          variants={fadeUp}
          className="mt-3 max-w-sm text-sm leading-relaxed text-ink-secondary"
        >
          The page you&apos;re looking for doesn&apos;t exist or has been moved.
          Let&apos;s get you back on track.
        </motion.p>

        {/* Actions */}
        <motion.div variants={fadeUp} className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <Link to="/">
            <Button size="lg">
              <ArrowLeft className="size-4" aria-hidden="true" />
              Back to home
            </Button>
          </Link>
          <Link to="/products">
            <Button variant="outline" size="lg">
              <Search className="size-4" aria-hidden="true" />
              Browse products
            </Button>
          </Link>
        </motion.div>
      </motion.div>
    </main>
  );
}
