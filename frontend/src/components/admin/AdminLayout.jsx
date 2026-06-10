import { useState } from 'react';
import { Outlet } from 'react-router-dom';
import { Menu } from 'lucide-react';
import { motion } from 'framer-motion';
import AdminSidebar from './AdminSidebar.jsx';
import { fadeIn } from '@/lib/motion.js';

export default function AdminLayout() {
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-full bg-bg-base">
      <AdminSidebar open={open} onClose={() => setOpen(false)} />

      <div className="flex min-h-screen flex-col lg:pl-64">
        {/* Mobile topbar — glass surface with subtle separator */}
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line-subtle bg-bg-elevated/80 px-4 backdrop-blur-md lg:hidden">
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label="Open navigation menu"
            className="grid size-9 place-items-center rounded-sm text-ink-secondary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
          >
            <Menu className="size-[18px]" />
          </button>

          {/* Wordmark */}
          <span className="text-sm font-semibold tracking-tight text-ink-primary">
            Lumen Admin
          </span>

          {/* Right-side spacer — future quick actions can slot here */}
          <div className="ml-auto" />
        </header>

        {/* Page content — restrained horizontal max-width is set inside AdminPage */}
        <motion.main
          variants={fadeIn}
          initial="hidden"
          animate="show"
          className="flex-1 px-4 py-6 lg:px-10 lg:py-10"
        >
          <Outlet />
        </motion.main>
      </div>
    </div>
  );
}
