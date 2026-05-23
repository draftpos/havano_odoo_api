{
    "name": "Havano Odoo API",
    "version": "19.0.2.0.6",
    "category": "Sales",
    "summary": "REST API for Havano POS: products, pharmacy, doctors, sales, invoices",
    "description": """
Havano POS Integration API - Production Ready
=============================================

Native Odoo session-based REST API for seamless POS synchronization.

**Features:**
- Simple username/password authentication (no token management)
- Products CRUD with intelligent SKU matching
- Customer management with email/phone deduplication
- Idempotent sales processing (no duplicates)
- Invoice creation and retrieval for POS display
- Payment registration and reconciliation
- Real-time stock queries
- Comprehensive error logging
- Graceful handling of missing optional modules
- Duplicate product name prevention
- Auto-generated ITEM CODE (starts from 101)
- Tax Inclusive by default for ZIMRA compliance
- Pharmacy products with dosage (when activated)
- Doctors CRUD for POS integration
- Dosages master data API for POS

**Workflow:**
1. POS sends sale -> Odoo creates order, invoice, delivery
2. POS gets invoice details to show customer
3. Customer pays -> POS sends payment with invoice reference
4. Odoo registers and reconciles payment
    """,
    "author": "Havano",
    "website": "https://www.havano.com",
    "license": "LGPL-3",
    "depends": [
        "base",
        "product",
        "sale_management",
        "account",
        "stock",
        "havano_all_in_one",
        "havano_product_bundle",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/sequence_data.xml",
        "views/res_users_views.xml",
        "views/api_docs_views.xml",
        "views/product_template_views.xml",
        # "views/res_company_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}