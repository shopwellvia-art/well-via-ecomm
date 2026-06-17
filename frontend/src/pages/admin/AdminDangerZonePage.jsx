import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { DatabaseTruncatePanel } from '@/components/admin/DatabaseTruncatePanel.jsx';

/**
 * Superadmin-only home for destructive maintenance. Route is gated by
 * RequireSuperadmin; each panel inside also self-gates on is_admin as defence
 * in depth. Add future destructive actions here.
 */
export default function AdminDangerZonePage() {
  return (
    <AdminPage
      title="Danger zone"
      description="Destructive, superadmin-only operations. Actions here are irreversible — proceed with care."
    >
      <DatabaseTruncatePanel />
    </AdminPage>
  );
}
