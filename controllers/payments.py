from odoo import http, fields, _  
from odoo.http import request
from odoo.exceptions import ValidationError
from .common import HavanoApiControllerMixin

import logging
_logger = logging.getLogger(__name__)


class HavanoPaymentsController(HavanoApiControllerMixin, http.Controller):
    
    # ================================================================
    # PAYMENT METHODS (Abstract)
    # ================================================================
    
    @http.route("/api/v1/payment-methods", auth="public", methods=["GET"], type="http", csrf=False)
    def get_payment_methods(self, **kwargs):
        """GET /api/v1/payment-methods - List all abstract payment methods."""
        return self._handle_route(lambda env: self._list_payment_methods(env))
    
    def _list_payment_methods(self, env):
        """Return abstract payment methods (what type of payment)."""
        payment_methods = env["account.payment.method"].search([])
        
        items = []
        for pm in payment_methods:
            items.append({
                "id": pm.id,
                "name": pm.name,
                "code": pm.code if hasattr(pm, 'code') else "",
                "payment_type": pm.payment_type if hasattr(pm, 'payment_type') else "inbound",
            })
        
        return self._success({
            "items": items,
            "total": len(items),
        })
    
    # ================================================================
    # PAYMENT METHOD LINES (Concrete instances with journals)
    # ================================================================
    
    @http.route("/api/v1/payment-method-lines", auth="public", methods=["GET"], type="http", csrf=False)
    def get_payment_method_lines(self, journal_type=None, active_only=True, **kwargs):
        """GET /api/v1/payment-method-lines - List payment method lines with journal details.
        
        Query params:
        - journal_type: string (optional) - 'cash', 'bank', 'sale', 'purchase'
        - active_only: bool (default True)
        """
        return self._handle_route(lambda env: self._list_payment_method_lines(env, journal_type, active_only))
    
    def _list_payment_method_lines(self, env, journal_type, active_only):
        """Return payment method lines (concrete payment instances with journals)."""
        
        # Build domain for journals
        journal_domain = []
        if journal_type:
            journal_domain.append(("type", "=", journal_type))
        if active_only:
            journal_domain.append(("active", "=", True))
        
        # Get all active journals of specified types
        journals = env["account.journal"].search(journal_domain)
        
        if not journals:
            # Return empty with message
            return self._success({
                "items": [],
                "total": 0,
                "message": "No journals found. Please configure cash/bank journals in Accounting."
            })
        
        # Get payment method lines linked to these journals
        payment_lines = env["account.payment.method.line"].search([
            ("journal_id", "in", journals.ids)
        ])
        
        items = []
        for line in payment_lines:
            items.append({
                "id": line.id,
                "name": line.name,
                "display_name": line.display_name,
                "sequence": line.sequence,
                
                # Payment Method details
                "payment_method_id": line.payment_method_id.id if line.payment_method_id else None,
                "payment_method_name": line.payment_method_id.name if line.payment_method_id else None,
                "payment_method_code": line.payment_method_id.code if line.payment_method_id and hasattr(line.payment_method_id, 'code') else None,
                "payment_type": line.payment_method_id.payment_type if line.payment_method_id and hasattr(line.payment_method_id, 'payment_type') else "inbound",
                
                # Journal details
                "journal_id": line.journal_id.id,
                "journal_name": line.journal_id.name,
                "journal_code": line.journal_id.code,
                "journal_type": line.journal_id.type,
                "journal_currency_id": line.journal_id.currency_id.id if line.journal_id.currency_id else None,
                "journal_currency_name": line.journal_id.currency_id.name if line.journal_id.currency_id else None,
                
                # Accounting accounts
                "payment_account_id": line.payment_account_id.id if line.payment_account_id else None,
                "payment_account_name": line.payment_account_id.name if line.payment_account_id else None,
            })
        
        # Sort by sequence
        items = sorted(items, key=lambda x: x.get("sequence", 0))
        
        return self._success({
            "items": items,
            "total": len(items),
            "journals_used": len(journals),
        })
    
    # ================================================================
    # POS-SPECIFIC PAYMENT METHODS (Combined view for POS app)
    # ================================================================
    
    @http.route("/api/v1/payment-methods/pos", auth="public", methods=["GET"], type="http", csrf=False)
    def get_pos_payment_methods(self, pos_config_id=None, **kwargs):
        """GET /api/v1/payment-methods/pos - Get payment methods configured for POS.
        
        This returns payment method lines that are active and linked to cash/bank journals.
        Optionally filter by specific POS config.
        """
        return self._handle_route(lambda env: self._get_pos_payment_methods(env, pos_config_id))
    
    def _get_pos_payment_methods(self, env, pos_config_id):
        """Return payment methods suitable for POS usage."""
        
        # If specific POS config provided, use its journals
        if pos_config_id:
            pos_config = env["pos.config"].browse(int(pos_config_id))
            if pos_config.exists():
                journals = pos_config.journal_ids
            else:
                journals = env["account.journal"].browse()
        else:
            # Default: get all cash and bank journals
            journals = env["account.journal"].search([
                ("type", "in", ["cash", "bank"]),
                ("active", "=", True)
            ])
        
        if not journals:
            return self._success({
                "items": [],
                "total": 0,
                "message": "No cash or bank journals found. Please configure a cash journal for POS."
            })
        
        # Get payment method lines for these journals
        payment_lines = env["account.payment.method.line"].search([
            ("journal_id", "in", journals.ids)
        ])
        
        items = []
        for line in payment_lines:
            items.append({
                "id": line.id,
                "name": line.name,
                "sequence": line.sequence,
                
                # POS-specific fields
                "is_cash": line.journal_id.type == "cash",
                "is_bank": line.journal_id.type == "bank",
                "journal_id": line.journal_id.id,
                "journal_name": line.journal_id.name,
                "journal_type": line.journal_id.type,
                
                # Payment method info
                "payment_method_id": line.payment_method_id.id if line.payment_method_id else None,
                "payment_method_name": line.payment_method_id.name if line.payment_method_id else None,
            })
        
        # Sort: cash first, then by sequence
        items = sorted(items, key=lambda x: (0 if x.get("is_cash") else 1, x.get("sequence", 0)))
        
        return self._success({
            "items": items,
            "total": len(items),
            "pos_config_id": pos_config_id,
        })
    
    # ================================================================
    # OVERDUE PAYMENTS
    # ================================================================
    
    @http.route("/api/v1/payments/overdue", auth="public", methods=["GET"], type="http", csrf=False)
    def get_overdue_payments(self, limit=100, **kwargs):
        """GET /api/v1/payments/overdue - Get overdue invoices."""
        return self._handle_route(lambda env: self._get_overdue(env, limit))
    
    def _get_overdue(self, env, limit):
        try:
            limit = min(int(limit), 500)
        except (ValueError, TypeError):
            limit = 100
        
        from datetime import date
        today = date.today()
        
        overdue = env["account.move"].search_read(
            domain=[
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("payment_state", "!=", "paid"),
                ("invoice_date_due", "<", today),
            ],
            fields=["id", "name", "partner_id", "amount_total", 
                   "amount_residual", "invoice_date_due", "payment_state"],
            limit=limit,
            order="invoice_date_due asc",
        )
        
        total_overdue = sum(inv.get("amount_residual", 0) for inv in overdue)
        
        return self._success({
            "items": overdue,
            "total": len(overdue),
            "total_amount_overdue": total_overdue,
        })
    
    # ================================================================
    # REGISTER PAYMENT
    # ================================================================
    
    @http.route("/api/v1/payments", auth="public", methods=["POST"], type="json", csrf=False)
    def register_payment(self, **kwargs):
        """POST /api/v1/payments - Register payment for invoice.
        
        Request body:
        {
            "invoice_id": 1,
            "amount": 150.00,
            "payment_date": "2026-05-14",
            "payment_method_line_id": 1,  // Optional: specific payment method line
            "journal_id": 1,              // Optional: specific journal
            "reference": "POS-001"        // Optional: reference number
        }
        """
        return self._handle_route(lambda env: self._register_payment(env))
    
    def _register_payment(self, env):
        data = self._parse_json_data()
        
        invoice_id = data.get("invoice_id")
        amount = data.get("amount")
        payment_date = data.get("payment_date")
        payment_method_line_id = data.get("payment_method_line_id")
        journal_id = data.get("journal_id")
        reference = data.get("reference", "")
        
        if not invoice_id or not amount:
            raise ValidationError(_("invoice_id and amount are required."))
        
        invoice = env["account.move"].browse(int(invoice_id))
        if not invoice.exists():
            raise ValidationError(_("Invoice #%s not found.") % invoice_id)
        
        # Determine journal
        journal = None
        if journal_id:
            journal = env["account.journal"].browse(int(journal_id))
        elif payment_method_line_id:
            payment_line = env["account.payment.method.line"].browse(int(payment_method_line_id))
            if payment_line.exists():
                journal = payment_line.journal_id
        
        if not journal:
            # Default: find first cash or bank journal
            journal = env["account.journal"].search([("type", "in", ["cash", "bank"])], limit=1)
        
        if not journal:
            raise ValidationError(_("No cash or bank journal found. Please configure one in Accounting."))
        
        # Get payment method
        payment_method = None
        if payment_method_line_id:
            payment_line = env["account.payment.method.line"].browse(int(payment_method_line_id))
            if payment_line.exists():
                payment_method = payment_line.payment_method_id
        
        if not payment_method:
            # Default: manual inbound payment method
            payment_method = env.ref("account.account_payment_method_manual_in", raise_if_not_found=False)
        
        # Create payment
        payment_vals = {
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": invoice.partner_id.id,
            "amount": float(amount),
            "date": payment_date or fields.Date.today(),
            "journal_id": journal.id,
            "ref": reference or f"Payment for {invoice.name}",
        }
        
        if payment_method:
            payment_vals["payment_method_id"] = payment_method.id
        
        payment = env["account.payment"].create(payment_vals)
        
        try:
            payment.action_post()
        except Exception as e:
            _logger.error("Failed to post payment: %s", str(e))
            raise ValidationError(_("Failed to post payment: %s") % str(e))
        
        # Reconcile with invoice
        try:
            (payment.line_ids + invoice.line_ids).reconcile()
        except Exception as e:
            _logger.warning("Could not auto-reconcile payment %s with invoice %s: %s", 
                          payment.id, invoice.id, str(e))
        
        return self._success({
            "payment_id": payment.id,
            "payment_name": payment.name,
            "invoice_id": invoice.id,
            "invoice_name": invoice.name,
            "amount": float(amount),
            "journal_id": journal.id,
            "journal_name": journal.name,
            "payment_method_line_id": payment_method_line_id,
        }, message=_("Payment registered and reconciled."))