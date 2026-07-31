/**
 * Admin route for cost rules and marketing spend.
 *
 * Thin on purpose. The screen itself lives in
 * `features/analytics/CostRulesAdmin.jsx` with the pure logic the vitest suite
 * asserts against; this file exists only to give it a route, a title, and the
 * one sentence that explains why the page exists at all.
 *
 * The description is not filler. Every margin metric in this product reports as
 * INCOMPLETE until a cost rule exists, and CAC/ROAS/payback stay unavailable
 * until spend is recorded — an admin who does not know that reads the finance
 * views as broken rather than as unconfigured, and there is no other surface
 * that tells them.
 */
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { CostRulesAdmin } from '@/features/analytics/CostRulesAdmin.jsx';

export default function AdminAnalyticsCostRulesPage() {
  return (
    <AdminPage
      title="Cost rules & marketing spend"
      description="What an order costs to fulfil, and what the store spends to win one. Margin, contribution, CAC, ROAS and payback are all computed from these numbers — until they are entered, those metrics report as incomplete rather than guessing."
    >
      <CostRulesAdmin />
    </AdminPage>
  );
}
