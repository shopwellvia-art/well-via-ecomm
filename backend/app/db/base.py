from app.models.address import Address  # noqa: F401
# Analytics v2 schema. Imported here purely so Alembic autogenerate sees the
# tables — nothing in the request path touches them while the analytics feature
# flags are off. Facts and rollups carry NO foreign keys by design so they stay
# independently truncatable and rebuildable (see app/models/analytics_base.py).
from app.models.analytics_control import (  # noqa: F401
    AnalyticsAlert,
    AnalyticsBudget,
    AnalyticsCostRule,
    AnalyticsEventOutbox,
    AnalyticsRecomputeQueue,
    AnalyticsSyncRun,
    AnalyticsTzGeneration,
)
from app.models.analytics_cx import AggCxDaily  # noqa: F401
from app.models.analytics_basket import AggBasketPairDaily  # noqa: F401
from app.models.analytics_facts import (  # noqa: F401
    AnalyticsOrderAdjustment,
    AnalyticsOrderLine,
    CartEvent,
    InventoryMovement,
)
from app.models.analytics_ga4 import AggGa4Daily  # noqa: F401
from app.models.analytics_loyalty import AggLoyaltyDaily  # noqa: F401
from app.models.analytics_rollups import (  # noqa: F401
    AggCustomerCohortMonthly,
    AggCustomerDaily,
    AggCustomerSnapshot,
    AggFunnelDaily,
    AggGeoDaily,
    AggInventoryDaily,
    AggOrderDaily,
    AggOrderHourly,
    AggPaymentDaily,
    AggProductDaily,
    AggPromoDaily,
    AggShipmentDaily,
)
from app.models.analytics_spend import AnalyticsMarketingSpend  # noqa: F401
from app.models.analytics_settlement import (  # noqa: F401
    AggSettlementDaily,
    PaymentSettlement,
)
from app.models.observability import RequestLog, SlowQuery  # noqa: F401
from app.models.audit import AuditEvent  # noqa: F401
from app.models.email_template import EmailTemplate  # noqa: F401
from app.models.base import Base  # noqa: F401
from app.models.coupon import Coupon, CouponUsage  # noqa: F401
from app.models.hero_slide import HeroSlide  # noqa: F401
from app.models.loyalty import (  # noqa: F401
    EarnRule,
    PointsTransaction,
    RedemptionTier,
    VipTier,
)
from app.models.order import Order, OrderItem  # noqa: F401
from app.models.order_address import OrderAddress  # noqa: F401
from app.models.order_payment import OrderPayment  # noqa: F401
from app.models.payment_method import PaymentMethod  # noqa: F401
from app.models.shipment import Shipment  # noqa: F401
from app.models.product import Category, Product, ProductImage  # noqa: F401
from app.models.rbac import Permission, Role  # noqa: F401
from app.models.referral import Referral  # noqa: F401
from app.models.review import Review  # noqa: F401
from app.models.system_setting import SystemSetting  # noqa: F401
from app.models.tax import Tax  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.user_security import UserSecurity  # noqa: F401
from app.models.wishlist import Wishlist  # noqa: F401
