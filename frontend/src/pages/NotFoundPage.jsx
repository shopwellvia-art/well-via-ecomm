import { Link } from 'react-router-dom';
import { Search } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';

export default function NotFoundPage() {
  return (
    <main className="mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-content flex-col items-center justify-center px-6 py-12 text-center">
      {/* 404 number */}
      <p className="text-[7rem] font-bold leading-none tracking-tight text-accent sm:text-[9rem]">
        404
      </p>

      {/* Headline */}
      <h1 className="mt-4 text-xl font-semibold text-ink-primary sm:text-2xl">
        Page not found
      </h1>

      {/* Support copy */}
      <p className="mt-2 max-w-sm text-sm leading-relaxed text-ink-secondary">
        The page you&apos;re looking for doesn&apos;t exist or has been moved.
        Let&apos;s get you back on track.
      </p>

      {/* Divider */}
      <div className="mt-8 h-px w-24 bg-line-subtle" />

      {/* Actions */}
      <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
        <Link to="/">
          <Button variant="primary" size="lg">
            Go to homepage
          </Button>
        </Link>
        <Link to="/products">
          <Button variant="outline" size="lg">
            <Search className="size-4" aria-hidden="true" />
            Browse products
          </Button>
        </Link>
      </div>
    </main>
  );
}
