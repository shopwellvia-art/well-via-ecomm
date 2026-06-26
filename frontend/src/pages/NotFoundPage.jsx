import { Link } from 'react-router-dom';
import { LeafMark } from '@/components/storefront/Logo';

export default function NotFoundPage() {
  return (
    <main className="min-h-[74vh] flex items-start justify-center pt-[clamp(40px,8vh,90px)] px-6 text-center">
      <div className="animate-rise">
        {/* Faded botanical mark */}
        <div className="mx-auto mb-4 w-fit opacity-50">
          <LeafMark size={64} />
        </div>

        {/* 404 number */}
        <div className="font-wserif text-[clamp(80px,12vw,140px)] leading-none text-wgreen">
          404
        </div>

        {/* Headline */}
        <h1 className="font-wserif font-medium text-[clamp(24px,3vw,34px)] mt-1.5 mb-2.5 text-wink">
          This page wandered off
        </h1>

        {/* Description */}
        <p className="text-[14.5px] text-wmuted max-w-[380px] mx-auto mb-6 font-light">
          The page you&apos;re looking for doesn&apos;t exist — but your next
          ritual does.
        </p>

        {/* Actions */}
        <div className="flex flex-wrap items-center justify-center gap-3">
          <Link
            to="/"
            className="inline-block rounded-full bg-wgreen px-[34px] py-[15px] text-[14px] text-white hover:bg-wgreen-dark transition-colors"
          >
            Back to Wellvia
          </Link>
          <Link
            to="/products"
            className="inline-block rounded-full border border-wline bg-wcard px-[34px] py-[15px] text-[14px] text-wink hover:border-wgreen hover:text-wgreen transition-colors"
          >
            Shop
          </Link>
        </div>
      </div>
    </main>
  );
}
