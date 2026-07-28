/**
 * Lucide component bindings for the analytics registry.
 *
 * This is the ONLY module in the feature that imports React code, and that is
 * the entire reason it exists. `registry.js`, `presentation.js`, `format.js`,
 * `filters.js` and `search.js` are pure data and logic, so the node-environment
 * vitest config (no jsdom, no @testing-library) can import them directly. The
 * moment a component reference lands in `presentation.js`, importing the
 * registry drags `lucide-react` — and with it JSX — into every unit test.
 *
 * So presentation stores icon *names* and this file resolves them. Do not
 * merge the two.
 *
 * `LUCIDE_ICONS` re-exports the icons by name rather than doing
 * `import * as lucide` because a namespace import defeats tree-shaking and
 * would pull all ~1,500 Lucide icons into the admin bundle.
 */
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Award,
  Banknote,
  Barcode,
  BarChart3,
  BellRing,
  Bike,
  Boxes,
  Briefcase,
  Building2,
  Calculator,
  ClipboardCheck,
  Coins,
  CreditCard,
  Factory,
  FileText,
  Filter,
  FlaskConical,
  Gauge,
  Gem,
  Globe,
  Grid2x2,
  Handshake,
  HeartPulse,
  Instagram,
  Landmark,
  Layers,
  LayoutDashboard,
  LayoutGrid,
  LifeBuoy,
  LineChart,
  Mail,
  MapPin,
  Megaphone,
  MessageCircle,
  MousePointerClick,
  Package,
  PackageCheck,
  PackageX,
  Percent,
  PieChart,
  Radio,
  RefreshCcw,
  RefreshCw,
  Rocket,
  Route,
  ScanSearch,
  Scale,
  Search,
  Settings2,
  Share2,
  ShieldAlert,
  ShoppingBag,
  ShoppingCart,
  Smartphone,
  Sparkles,
  Star,
  Store,
  Tag,
  Tags,
  Target,
  TicketPercent,
  TrendingUp,
  Truck,
  Undo2,
  UserMinus,
  UserPlus,
  Users,
  Wallet,
  Wand2,
  Warehouse,
  XCircle,
} from 'lucide-react';
import { MODULE_PRESENTATION, VIEW_PRESENTATION, modulePresentation, viewPresentation } from './presentation.js';
import { MODULES, allViews, moduleNavItems, viewNavItems } from './registry.js';

/** Every icon name `presentation.js` is allowed to use. */
export const LUCIDE_ICONS = {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Award,
  Banknote,
  Barcode,
  BarChart3,
  BellRing,
  Bike,
  Boxes,
  Briefcase,
  Building2,
  Calculator,
  ClipboardCheck,
  Coins,
  CreditCard,
  Factory,
  FileText,
  Filter,
  FlaskConical,
  Gauge,
  Gem,
  Globe,
  Grid2x2,
  Handshake,
  HeartPulse,
  Instagram,
  Landmark,
  Layers,
  LayoutDashboard,
  LayoutGrid,
  LifeBuoy,
  LineChart,
  Mail,
  MapPin,
  Megaphone,
  MessageCircle,
  MousePointerClick,
  Package,
  PackageCheck,
  PackageX,
  Percent,
  PieChart,
  Radio,
  RefreshCcw,
  RefreshCw,
  Rocket,
  Route,
  ScanSearch,
  Scale,
  Search,
  Settings2,
  Share2,
  ShieldAlert,
  ShoppingBag,
  ShoppingCart,
  Smartphone,
  Sparkles,
  Star,
  Store,
  Tag,
  Tags,
  Target,
  TicketPercent,
  TrendingUp,
  Truck,
  Undo2,
  UserMinus,
  UserPlus,
  Users,
  Wallet,
  Wand2,
  Warehouse,
  XCircle,
};

/** Fallback for a slug whose overlay entry names an icon we do not import. */
export const FALLBACK_ICON = BarChart3;

function resolve(name) {
  return LUCIDE_ICONS[name] ?? FALLBACK_ICON;
}

/** slug → Lucide component, for all 12 modules. */
export const MODULE_ICONS = Object.fromEntries(
  Object.keys(MODULE_PRESENTATION).map((slug) => [slug, resolve(modulePresentation(slug).icon)]),
);

/** slug → Lucide component, for all 73 views. */
export const VIEW_ICONS = Object.fromEntries(
  Object.keys(VIEW_PRESENTATION).map((slug) => [slug, resolve(viewPresentation(slug).icon)]),
);

export function getModuleIcon(slug) {
  return MODULE_ICONS[slug] ?? FALLBACK_ICON;
}

export function getViewIcon(slug) {
  return VIEW_ICONS[slug] ?? FALLBACK_ICON;
}

/** Sidebar-ready nav items with real components — what `AdminSidebar` wants. */
export function analyticsNavItems() {
  return moduleNavItems(MODULE_ICONS);
}

/** Second-level nav items for one module, with real components. */
export function analyticsViewNavItems(moduleSlug) {
  return viewNavItems(moduleSlug, VIEW_ICONS);
}

/** Re-exported so a consumer needs one import, not three. */
export { MODULES, allViews };
