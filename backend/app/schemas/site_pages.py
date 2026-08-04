"""Pydantic schemas for the storefront company/content pages.

The six footer "About" links (Contact Us, About Us, Careers, Wellvia Stories,
Press, Corporate Information) are backed by a single JSON document stored in
the `site_pages` table. DEFAULT_SITE_PAGES is the single source of truth for
factory content — the service falls back to it when no row exists or when a
top-level page key is absent.

The shape here is mirrored 1:1 by the frontend at
`frontend/src/features/site-pages/defaults.js`. Keep them in sync.
"""
from __future__ import annotations

from pydantic import ConfigDict, field_validator

from app.schemas._validators import validate_safe_url
from app.schemas.base import AppSchema

# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


class Hero(AppSchema):
    """The page header — eyebrow line, big title, supporting subtitle.

    `image` is the optional banner behind the copy. It exists so a page whose
    header is photography rather than text can still be changed from the admin:
    without it the only way to alter the banner is to redeploy the frontend.
    `title` allows "" because a banner with the wording already baked into the
    artwork must be able to render with no text overlaid on top of it.
    """

    model_config = ConfigDict(from_attributes=True)

    eyebrow: str = ""
    title: str = ""
    subtitle: str = ""
    image: str = ""


class FeatureItem(AppSchema):
    """Icon + title + body — used for values, perks, etc."""

    model_config = ConfigDict(from_attributes=True)

    icon: str = "Sparkles"
    title: str
    text: str = ""


class Stat(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    value: str
    label: str


class ProseSection(AppSchema):
    """A heading + a body. Body paragraphs are separated by blank lines."""

    model_config = ConfigDict(from_attributes=True)

    heading: str
    body: str


class ContactMethod(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    icon: str = "Mail"
    title: str
    detail: str
    href: str = ""

    @field_validator("href")
    @classmethod
    def _check_href(cls, v: str) -> str:
        return validate_safe_url(v)


class ContactForm(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    heading: str
    note: str
    success: str


class Office(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    city: str
    lines: list[str] = []


class JobOpening(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    title: str
    department: str = ""
    location: str = ""
    type: str = ""
    url: str = ""

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return validate_safe_url(v)


class Story(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    title: str
    excerpt: str = ""
    image: str = ""
    category: str = ""
    date: str = ""
    url: str = ""

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return validate_safe_url(v)


class PressRelease(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    date: str = ""
    title: str
    source: str = ""
    url: str = ""

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return validate_safe_url(v)


class PressContact(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    heading: str
    email: str = ""
    phone: str = ""


class Leader(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    name: str
    title: str = ""
    image: str = ""


class Download(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    label: str
    url: str = ""

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return validate_safe_url(v)


class CorporateEntity(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    name: str
    cin: str = ""
    address_lines: list[str] = []
    email: str = ""
    phone: str = ""


# ---------------------------------------------------------------------------
# Per-page documents
# ---------------------------------------------------------------------------


class AboutPage(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = True
    hero: Hero
    intro: list[str] = []
    # Section headings above the story and values blocks. They were fixed
    # strings in the page, so an admin who repurposed either list could not
    # retitle it.
    story_label: str = "Our story"
    values_label: str = "What we value"
    stats: list[Stat] = []
    values: list[FeatureItem] = []
    mission: ProseSection


class ContactPage(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = True
    hero: Hero
    intro: str = ""
    methods: list[ContactMethod] = []
    form: ContactForm
    # Support-hours block under the enquiry form. Both default to "" so an
    # admin can clear them to hide the block. They must exist here: Pydantic
    # drops unknown keys silently, so a field the admin form sends but the
    # schema does not declare would appear to save and then vanish.
    hours: str = ""
    response_note: str = ""
    offices: list[Office] = []


class CareersPage(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = True
    hero: Hero
    intro: str = ""
    perks: list[FeatureItem] = []
    openings: list[JobOpening] = []
    culture: ProseSection


class StoriesPage(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = True
    hero: Hero
    intro: str = ""
    posts: list[Story] = []


class PressPage(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = True
    hero: Hero
    intro: str = ""
    releases: list[PressRelease] = []
    contact: PressContact
    kit_url: str = ""

    @field_validator("kit_url")
    @classmethod
    def _check_kit_url(cls, v: str) -> str:
        return validate_safe_url(v)


class CorporatePage(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = True
    hero: Hero
    sections: list[ProseSection] = []
    leadership: list[Leader] = []
    entity: CorporateEntity
    downloads: list[Download] = []


class PolicyPage(AppSchema):
    """Legal / customer-care policy page — a hero, an optional "last updated"
    line, and a list of heading+body sections. Shared by the Privacy, Terms,
    Refund/Cancellation and Shipping pages."""

    model_config = ConfigDict(from_attributes=True)

    enabled: bool = True
    hero: Hero
    updated: str = ""
    sections: list[ProseSection] = []


# ---------------------------------------------------------------------------
# Canonical defaults — single source of truth used by SitePagesService
# ---------------------------------------------------------------------------

# Registered-office lines are statutory details — left empty on purpose so an
# admin fills in the real address via Admin → Pages → Corporate Information.
_REGISTERED_ADDRESS: list[str] = []

DEFAULT_SITE_PAGES: dict = {
    "about": {
        "enabled": True,
        "hero": {
            "eyebrow": "Our Story",
            "title": "Wellness, made simple and honest",
            "subtitle": (
                "Wellvia began with a simple belief — feeling your best shouldn't "
                "be complicated. We craft science-backed wellness essentials that "
                "fit effortlessly into everyday life."
            ),
        },
        "intro": [
            "Wellvia was founded to reimagine everyday wellness — nutrition and "
            "self-care that actually taste good, work as promised, and are easy to "
            "stick with. What started with a handful of thoughtfully formulated "
            "gummies is now a community that believes small daily habits create "
            "lasting change.",
            "Today we help people across India take better care of themselves, but "
            "our promise hasn't changed: clean, effective formulas, honest labels, "
            "and support from real people who genuinely care about your wellbeing.",
        ],
        # Ships EMPTY, and the page hides the whole row when it is. These were
        # template placeholders — "1M+ Happy customers", "50+ Wellness
        # formulas", "4.8/5 Average rating" — published on a store with no
        # orders, nine products and zero reviews. Unsubstantiated figures like
        # a made-up average rating are exactly what the CCPA misleading-
        # advertisement guidelines and the ASCI code target, so the default is
        # to claim nothing. An admin adds real numbers once they exist.
        "stats": [],
        "values": [
            {
                "icon": "Heart",
                "title": "Customer obsessed",
                "text": "Every formula starts with the people we serve. Real support, easy returns, and no fine print.",
            },
            {
                "icon": "Leaf",
                "title": "Clean & natural",
                "text": "We choose clean, thoughtfully sourced ingredients — no unnecessary fillers, and we're honest about what goes in.",
            },
            {
                "icon": "ShieldCheck",
                "title": "Science-backed",
                "text": "Every product is built on real research and tested for quality and safety you can trust.",
            },
            {
                "icon": "Sparkles",
                "title": "Made to enjoy",
                "text": "Wellness should feel like a treat, not a chore — delicious formats you'll actually look forward to.",
            },
        ],
        "mission": {
            "heading": "Our mission",
            "body": (
                "To make effective, delightful wellness accessible to everyone — "
                "and to treat every customer and community we touch with genuine "
                "care.\n\n"
                "We're building a wellness brand we'd be proud to use ourselves, "
                "every single day."
            ),
        },
    },
    "contact": {
        "enabled": True,
        # hero-contact.png already carries "Contact Us" and the supporting line
        # as pixels, so the text fields ship EMPTY — filling them here would
        # print the same words twice, once from the artwork and once from the
        # overlay. They remain fully editable; an admin who uploads a text-free
        # banner can then set them and they render.
        "hero": {
            "eyebrow": "",
            "title": "",
            "subtitle": "",
            "image": "/hero-contact.png",
        },
        "intro": (
            "Reach us through any of the channels below, or drop us a message "
            "and we'll get back within one business day."
        ),
        "methods": [
            {
                "icon": "Mail",
                "title": "Email us",
                "detail": "support@shopwellvia.in",
                "href": "mailto:support@shopwellvia.in",
            },
            # Phone and office address are left blank for an admin to fill in.
            {
                "icon": "Phone",
                "title": "Call us",
                "detail": "",
                "href": "",
            },
            {
                "icon": "MessageCircle",
                "title": "Live chat",
                "detail": "Mon–Sat, 9am – 8pm IST",
                "href": "",
            },
            {
                "icon": "MapPin",
                "title": "Visit us",
                "detail": "",
                "href": "",
            },
        ],
        "form": {
            "heading": "Send us a message",
            "note": "We typically reply within one business day.",
            "success": "Thanks for reaching out — we'll be in touch shortly.",
        },
        # Support-hours block. Must match the frontend defaults exactly: the API
        # value wins the frontend's merge, so an empty string here would blank
        # the block on the live page even though the frontend has copy for it.
        "hours": "Monday – Saturday (9:00 AM – 6:00 PM IST)",
        "response_note": "We aim to respond to all queries within 24–48 business hours.",
        # Office addresses are left empty on purpose — an admin adds the real ones.
        "offices": [],
    },
    "careers": {
        "enabled": True,
        "hero": {
            "eyebrow": "Careers",
            "title": "Build the future of everyday wellness",
            "subtitle": "Join a team that cares deeply about formulation, customers and each other.",
        },
        "intro": (
            "We're a curious, kind and ambitious bunch. If you want to do the "
            "best work of your career alongside people who'll cheer you on, "
            "we'd love to meet you."
        ),
        "perks": [
            {
                "icon": "HeartHandshake",
                "title": "People first",
                "text": "Generous leave, parental support, and a culture that respects your life outside work.",
            },
            {
                "icon": "TrendingUp",
                "title": "Grow fast",
                "text": "Learning budgets, mentorship and real ownership from day one.",
            },
            {
                "icon": "Gift",
                "title": "Great perks",
                "text": "Health cover, employee discounts and meaningful equity.",
            },
            {
                "icon": "Globe",
                "title": "Flexible & remote-friendly",
                "text": "Work where you do your best — hybrid by default, fully flexible hours.",
            },
        ],
        "openings": [
            {
                "title": "Senior Frontend Engineer",
                "department": "Engineering",
                "location": "Bengaluru / Remote",
                "type": "Full-time",
                "url": "mailto:careers@shopwellvia.in?subject=Senior%20Frontend%20Engineer",
            },
            {
                "title": "Product Designer",
                "department": "Design",
                "location": "Bengaluru",
                "type": "Full-time",
                "url": "mailto:careers@shopwellvia.in?subject=Product%20Designer",
            },
            {
                "title": "Customer Experience Lead",
                "department": "Operations",
                "location": "Remote",
                "type": "Full-time",
                "url": "mailto:careers@shopwellvia.in?subject=Customer%20Experience%20Lead",
            },
        ],
        "culture": {
            "heading": "Life at Wellvia",
            "body": (
                "We move quickly without losing the plot. We disagree openly, "
                "decide clearly, and back each other once we commit.\n\n"
                "Most of all, we keep the customer at the center of everything — "
                "because the best ideas come from genuinely caring about the "
                "people we build for."
            ),
        },
    },
    "stories": {
        "enabled": True,
        "hero": {
            "eyebrow": "Wellvia Stories",
            "title": "Ideas, people and behind-the-scenes",
            "subtitle": "Notes from our formulators, customers and the journey of building Wellvia.",
        },
        "intro": "Long reads, short notes and everything in between.",
        "posts": [
            {
                "title": "How we choose ingredients we're proud of",
                "excerpt": "A look inside the sourcing, testing and tough calls behind every Wellvia gummy.",
                "image": "",
                "category": "Behind the scenes",
                "date": "2026-05-12",
                "url": "",
            },
            {
                "title": "Meet the formulators behind our sleep gummies",
                "excerpt": "The nutritionists and food scientists who shaped our bestselling routine.",
                "image": "",
                "category": "People",
                "date": "2026-04-28",
                "url": "",
            },
            {
                "title": "Small changes, big impact: our packaging redesign",
                "excerpt": "How we rethought our jars and cartons without compromising on the unboxing.",
                "image": "",
                "category": "Sustainability",
                "date": "2026-03-09",
                "url": "",
            },
        ],
    },
    "press": {
        "enabled": True,
        "hero": {
            "eyebrow": "Press",
            "title": "Wellvia in the news",
            "subtitle": "Announcements, media coverage and resources for journalists.",
        },
        "intro": "For interviews, assets or comment, reach our communications team below.",
        # Placeholder announcements — replace with real coverage via Admin -> Pages.
        "releases": [
            {
                "date": "",
                "title": "Announcement title",
                "source": "Company announcement",
                "url": "",
            },
            {
                "date": "",
                "title": "Product launch announcement",
                "source": "Company announcement",
                "url": "",
            },
            {
                "date": "",
                "title": "Media coverage headline",
                "source": "",
                "url": "",
            },
        ],
        "contact": {
            "heading": "Media enquiries",
            "email": "press@shopwellvia.in",
            "phone": "",
        },
        "kit_url": "",
    },
    "corporate": {
        "enabled": True,
        "hero": {
            "eyebrow": "Corporate Information",
            "title": "About the company behind Wellvia",
            "subtitle": "Governance, leadership and statutory details.",
        },
        "sections": [
            {
                "heading": "Company overview",
                "body": (
                    "Wellvia is a direct-to-consumer wellness brand based in India, "
                    "operating the shopwellvia.in storefront. We make wellness gummies "
                    "across sleep, immunity, beauty, gut health, multivitamin and "
                    "omega ranges.\n\n"
                    "This page brings together the statutory and governance "
                    "information required under applicable law."
                ),
            },
            {
                "heading": "Compliance & grievance",
                "body": (
                    "In accordance with the Consumer Protection (E-Commerce) Rules, "
                    "our Grievance Officer can be reached at grievance@shopwellvia.in. "
                    "We endeavour to acknowledge complaints within 48 hours and "
                    "resolve them within one month."
                ),
            },
        ],
        # Leadership is left empty on purpose — an admin adds real names/photos.
        "leadership": [],
        # Statutory identifiers are intentionally blank: an admin fills in the
        # registered legal name, CIN, office address and phone via Admin -> Pages.
        "entity": {
            "name": "",
            "cin": "",
            "address_lines": _REGISTERED_ADDRESS,
            "email": "compliance@shopwellvia.in",
            "phone": "",
        },
        "downloads": [
            {"label": "Certificate of Incorporation", "url": ""},
            {"label": "Terms of Use", "url": "/terms"},
            {"label": "Privacy Policy", "url": "/privacy"},
        ],
    },
    "privacy": {
        "enabled": True,
        "hero": {
            "eyebrow": "Legal",
            "title": "Privacy Policy",
            "subtitle": (
                "How Wellvia collects, uses, and protects the personal "
                "information you share with us."
            ),
        },
        "updated": "Last updated: 11 July 2026",
        "sections": [
            {
                "heading": "Information we collect",
                "body": (
                    "We collect information you provide directly — such as your "
                    "name, email, phone number, shipping address, and payment "
                    "details — when you create an account, place an order, or "
                    "contact us. We also automatically collect limited technical "
                    "data such as your device, browser, and how you use our site."
                ),
            },
            {
                "heading": "How we use your information",
                "body": (
                    "We use your information to process and deliver orders, "
                    "provide customer support, personalise your experience, send "
                    "order updates, and — where you have opted in — share offers "
                    "and wellness content. We never sell your personal data."
                ),
            },
            {
                "heading": "Cookies & tracking",
                "body": (
                    "We use cookies and similar technologies to keep you signed "
                    "in, remember your cart, and understand how our store is used "
                    "so we can improve it. You can control cookies through your "
                    "browser settings."
                ),
            },
            {
                "heading": "Sharing & disclosure",
                "body": (
                    "We share information only with the partners who help us run "
                    "our business — payment processors, logistics and delivery "
                    "providers, and analytics services — and only as needed. We "
                    "may also disclose information where required by law."
                ),
            },
            {
                "heading": "Data security",
                "body": (
                    "We use industry-standard safeguards to protect your data. No "
                    "method of transmission over the internet is completely "
                    "secure, but we work hard to protect your information and "
                    "review our practices regularly."
                ),
            },
            {
                "heading": "Your rights",
                "body": (
                    "You may access, correct, or delete your personal information, "
                    "and opt out of marketing at any time, by updating your "
                    "account or contacting us at support@shopwellvia.in."
                ),
            },
        ],
    },
    "terms": {
        "enabled": True,
        "hero": {
            "eyebrow": "Legal",
            "title": "Terms & Conditions",
            "subtitle": (
                "The terms that govern your use of the Wellvia website and the "
                "purchases you make with us."
            ),
        },
        "updated": "Last updated: 11 July 2026",
        "sections": [
            {
                "heading": "Acceptance of terms",
                "body": (
                    "By accessing or using the Wellvia website and placing an "
                    "order, you agree to be bound by these Terms & Conditions. If "
                    "you do not agree, please do not use the site."
                ),
            },
            {
                "heading": "Use of the website",
                "body": (
                    "You agree to use the site only for lawful purposes and not to "
                    "misuse it, interfere with its operation, or attempt to access "
                    "it in any unauthorised way. You are responsible for keeping "
                    "your account credentials secure."
                ),
            },
            {
                "heading": "Products & pricing",
                "body": (
                    "We aim to describe and price every product accurately. "
                    "Colours, packaging, and availability may vary, and we reserve "
                    "the right to correct errors, change prices, or update product "
                    "information at any time before your order is confirmed."
                ),
            },
            {
                "heading": "Health disclaimer",
                "body": (
                    "Wellvia products are dietary supplements and are not intended "
                    "to diagnose, treat, cure, or prevent any disease. Please read "
                    "the label and consult a qualified healthcare professional "
                    "before use, especially if you are pregnant, nursing, or on "
                    "medication."
                ),
            },
            {
                "heading": "Orders & payment",
                "body": (
                    "All orders are subject to acceptance and availability. Payment "
                    "must be completed through our approved payment methods before "
                    "an order is dispatched. We may cancel any order in the event "
                    "of suspected fraud or pricing errors."
                ),
            },
            {
                "heading": "Limitation of liability",
                "body": (
                    "To the fullest extent permitted by law, Wellvia shall not be "
                    "liable for any indirect or consequential loss arising from the "
                    "use of our site or products beyond the value of the order in "
                    "question."
                ),
            },
            {
                "heading": "Governing law",
                "body": (
                    "These terms are governed by the laws of India, and any "
                    "disputes shall be subject to the exclusive jurisdiction of the "
                    "courts at our registered office location."
                ),
            },
        ],
    },
    "refund": {
        "enabled": True,
        "hero": {
            "eyebrow": "Customer Care",
            "title": "Refund & Cancellation Policy",
            "subtitle": (
                "How order cancellations, returns, and refunds work at Wellvia."
            ),
        },
        "updated": "Last updated: 11 July 2026",
        "sections": [
            {
                "heading": "Order cancellation",
                "body": (
                    "You can cancel your order any time before it is dispatched for "
                    "a full refund. Once an order has shipped, it can no longer be "
                    "cancelled, but you may be eligible to return it under the terms "
                    "below."
                ),
            },
            {
                "heading": "Returns & eligibility",
                "body": (
                    "As our products are consumable wellness items, returns are "
                    "accepted only for products that arrive damaged, defective, "
                    "expired, or incorrect. Requests must be raised within 7 days "
                    "of delivery with the item unopened and in its original "
                    "packaging."
                ),
            },
            {
                "heading": "Non-returnable items",
                "body": (
                    "For hygiene and safety reasons, opened or used products, and "
                    "items marked as final sale, cannot be returned unless they "
                    "were received damaged or defective."
                ),
            },
            {
                "heading": "Refund process & timelines",
                "body": (
                    "Once your return is received and inspected, we will notify you "
                    "of approval. Approved refunds are processed to your original "
                    "payment method within 5–7 business days."
                ),
            },
            {
                "heading": "Damaged or incorrect items",
                "body": (
                    "If you receive a damaged, defective, or wrong item, contact us "
                    "at support@shopwellvia.in within 48 hours of delivery with your "
                    "order number and a photo, and we will arrange a replacement or "
                    "refund at no extra cost."
                ),
            },
        ],
    },
    "shipping": {
        "enabled": True,
        "hero": {
            "eyebrow": "Customer Care",
            "title": "Shipping Policy",
            "subtitle": (
                "Delivery timelines, charges, and coverage for your Wellvia "
                "orders."
            ),
        },
        "updated": "Last updated: 11 July 2026",
        "sections": [
            {
                "heading": "Order processing",
                "body": (
                    "Orders are processed within 1–2 business days. Orders placed "
                    "on weekends or public holidays are processed on the next "
                    "business day. You will receive a confirmation once your order "
                    "is dispatched."
                ),
            },
            {
                "heading": "Delivery timelines",
                "body": (
                    "Once dispatched, orders are typically delivered within 3–7 "
                    "business days depending on your location. Remote areas may "
                    "take a little longer."
                ),
            },
            {
                "heading": "Shipping charges",
                "body": (
                    "Shipping charges, if any, are calculated at checkout based on "
                    "your order value and delivery location. Orders above the "
                    "eligible value qualify for free shipping."
                ),
            },
            {
                "heading": "Order tracking",
                "body": (
                    "As soon as your order ships, we will email you a tracking "
                    "link. You can also track your order any time from the "
                    "\"My Orders\" section of your account."
                ),
            },
            {
                "heading": "Delivery areas",
                "body": (
                    "We currently ship across India. If we are unable to deliver "
                    "to your pin code, you will be notified at checkout."
                ),
            },
            {
                "heading": "Delays",
                "body": (
                    "Occasionally, deliveries may be delayed due to weather, "
                    "logistics, or events beyond our control. We will keep you "
                    "informed and do our best to get your order to you quickly."
                ),
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Top-level document schemas
# ---------------------------------------------------------------------------


class SitePagesRead(AppSchema):
    """Full company-pages document returned by GET /site-pages."""

    model_config = ConfigDict(from_attributes=True)

    about: AboutPage
    contact: ContactPage
    careers: CareersPage
    stories: StoriesPage
    press: PressPage
    corporate: CorporatePage
    privacy: PolicyPage
    terms: PolicyPage
    refund: PolicyPage
    shipping: PolicyPage


class SitePagesUpdate(AppSchema):
    """PUT body — full replace semantics.

    Every top-level page defaults to its canonical default so a client that
    omits a page doesn't accidentally blank it out. In practice the admin UI
    sends the complete document back.
    """

    model_config = ConfigDict(from_attributes=True)

    about: AboutPage = AboutPage(**DEFAULT_SITE_PAGES["about"])
    contact: ContactPage = ContactPage(**DEFAULT_SITE_PAGES["contact"])
    careers: CareersPage = CareersPage(**DEFAULT_SITE_PAGES["careers"])
    stories: StoriesPage = StoriesPage(**DEFAULT_SITE_PAGES["stories"])
    press: PressPage = PressPage(**DEFAULT_SITE_PAGES["press"])
    corporate: CorporatePage = CorporatePage(**DEFAULT_SITE_PAGES["corporate"])
    privacy: PolicyPage = PolicyPage(**DEFAULT_SITE_PAGES["privacy"])
    terms: PolicyPage = PolicyPage(**DEFAULT_SITE_PAGES["terms"])
    refund: PolicyPage = PolicyPage(**DEFAULT_SITE_PAGES["refund"])
    shipping: PolicyPage = PolicyPage(**DEFAULT_SITE_PAGES["shipping"])
