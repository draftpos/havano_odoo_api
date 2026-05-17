# controllers/payments.py
from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError
from .common import HavanoApiControllerMixin
from odoo.exceptions import MissingError

import logging
_logger = logging.getLogger(__name__)


class HavanoPaymentsController(HavanoApiControllerMixin, http.Controller):

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
            "payment_method": "cash",
            "reference": "POS-001",
            "payment_date": "2026-05-17"  // optional
        }
        """
        return self._handle_route(lambda env: self._register_payment(env))

    def _register_payment(self, env):
        data = self._parse_json_data()

        invoice_id = data.get("invoice_id")
        amount = data.get("amount")
        payment_method = data.get("payment_method", "cash")
        reference = data.get("reference", "")
        payment_date = data.get("payment_date")

        if not invoice_id or not amount:
            raise ValidationError(_("invoice_id and amount are required."))

        if amount <= 0:
            raise ValidationError(_("Amount must be greater than zero."))

        invoice = env["account.move"].browse(int(invoice_id))
        if not invoice.exists():
            raise ValidationError(_("Invoice #%s not found.") % invoice_id)

        # Check if invoice is already paid
        if invoice.payment_state == "paid":
            return self._success({
                "invoice_id": invoice.id,
                "invoice_name": invoice.name,
                "already_paid": True,
                "amount_total": invoice.amount_total,
                "amount_residual": invoice.amount_residual,
                "payment_state": invoice.payment_state,
            }, message="Invoice already paid.")

        # Check if amount exceeds residual
        if amount > invoice.amount_residual:
            raise ValidationError(_("Payment amount (%.2f) exceeds invoice residual (%.2f).") % (amount, invoice.amount_residual))

        # Determine journal based on payment method
        journal = self._get_payment_journal(env, payment_method)

        # Get payment method record
        payment_method_record = env.ref("account.account_payment_method_manual_in", raise_if_not_found=False)

        # Create payment
        payment = env["account.payment"].sudo().create({
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": invoice.partner_id.id,
            "amount": float(amount),
            "date": fields.Date.from_string(payment_date) if payment_date else fields.Date.today(),
            "journal_id": journal.id,
            "payment_method_id": payment_method_record.id if payment_method_record else False,
            "ref": reference or f"Payment for {invoice.name}",
            "invoice_ids": [(6, 0, [invoice.id])],
        })

        # Post payment
        try:
            payment.action_post()
            _logger.info("Payment posted: id=%s, amount=%s, invoice=%s", payment.id, amount, invoice.name)
        except Exception as e:
            _logger.error("Failed to post payment: %s", str(e))
            raise ValidationError(_("Failed to post payment: %s") % str(e))

        # Reconcile with invoice
        try:
            (payment.line_ids + invoice.line_ids).reconcile()
            _logger.info("Payment reconciled with invoice %s", invoice.name)
        except Exception as e:
            _logger.warning("Could not auto-reconcile payment %s with invoice %s: %s", payment.id, invoice.id, str(e))

        # Refresh invoice
        invoice.invalidate_recordset()

        return self._success({
            "payment_id": payment.id,
            "payment_name": payment.name,
            "invoice_id": invoice.id,
            "invoice_name": invoice.name,
            "amount_paid": amount,
            "amount_residual": invoice.amount_residual,
            "payment_state": invoice.payment_state,
            "journal_id": journal.id,
            "journal_name": journal.name,
        }, message="Payment registered and reconciled.")

    # ================================================================
    # GET INVOICE PAYMENT STATUS
    # ================================================================

    @http.route("/api/v1/invoices/<int:invoice_id>/payment-status", auth="public", methods=["GET"], type="json", csrf=False)
    def get_payment_status(self, invoice_id, **kwargs):
        """GET /api/v1/invoices/:id/payment-status - Get payment status for invoice."""
        return self._handle_route(lambda env: self._get_payment_status(env, invoice_id))

    def _get_payment_status(self, env, invoice_id):
        invoice = env["account.move"].browse(invoice_id)
        if not invoice.exists():
            raise MissingError(_("Invoice #%s not found.") % invoice_id)

        # Get payments for this invoice
        payments = env["account.payment"].search([
            ("invoice_ids", "in", invoice.id),
            ("state", "=", "posted"),
        ])

        return self._success({
            "invoice_id": invoice.id,
            "invoice_name": invoice.name,
            "amount_total": invoice.amount_total,
            "amount_residual": invoice.amount_residual,
            "payment_state": invoice.payment_state,
            "payments": [{
                "id": p.id,
                "name": p.name,
                "amount": p.amount,
                "date": str(p.date),
                "journal_name": p.journal_id.name,
            } for p in payments],
        })

    # ================================================================
    # HELPER METHODS
    # ================================================================

    def _get_payment_journal(self, env, method):
        """Find appropriate journal for the payment method."""
        Journal = env["account.journal"].sudo()

        if method == "cash":
            journal = Journal.search([
                ("type", "=", "cash"),
                ("company_id", "=", env.company.id),
            ], limit=1)
            if not journal:
                journal = Journal.search([
                    ("type", "=", "bank"),
                    ("company_id", "=", env.company.id),
                ], limit=1)
        else:
            journal = Journal.search([
                ("type", "=", "bank"),
                ("company_id", "=", env.company.id),
            ], limit=1)

        if not journal:
            journal = Journal.search([
                ("type", "in", ["bank", "cash"]),
                ("company_id", "=", env.company.id),
            ], limit=1)

        if not journal:
            raise ValidationError(_(
                "No cash or bank journal found. Please configure one in Accounting."
            ))

        return journal

    # ================================================================
    # PAYMENT METHODS (Abstract)
    # ================================================================

    @http.route("/api/v1/payment-methods", auth="public", methods=["GET"], type="http", csrf=False)
    def get_payment_methods(self, **kwargs):
        """GET /api/v1/payment-methods - List all abstract payment methods."""
        return self._handle_route(lambda env: self._list_payment_methods(env))

    def _list_payment_methods(self, env):
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