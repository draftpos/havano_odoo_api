{
    "name": "Havano Odoo API",
    "version": "19.0.2.0.0",
    "category": "Sales",
    "summary": "Production-ready REST API for Havano POS with native Odoo authentication",
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
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/api_docs_views.xml",
        "views/res_users_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}