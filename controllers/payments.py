from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import MissingError, ValidationError
from .common import HavanoApiControllerMixin

import logging

payment_logger = logging.getLogger('havano.payments')
_logger = logging.getLogger(__name__)


class HavanoPaymentsController(HavanoApiControllerMixin, http.Controller):

    @http.route("/api/v1/payments", auth="public", methods=["POST"], type="http", csrf=False)
    def register_payment(self, **kwargs):
        payment_logger.info("="*60)
        payment_logger.info("PAYMENT REQUEST RECEIVED")
        return self._handle_route(lambda env: self._register_payment(env))

    def _register_payment(self, env):
        payment_logger.info("Processing payment registration...")
        
        data = self._parse_json_data()
        payment_logger.info(f"Request data: {data}")
        
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
            raise MissingError(_("Invoice #%s not found.") % invoice_id)

        if invoice.payment_state == "paid":
            return self._success({
                "invoice_id": invoice.id,
                "invoice_name": invoice.name,
                "already_paid": True,
            }, message="Invoice already paid.")

        if amount > invoice.amount_residual:
            raise ValidationError(_("Payment amount exceeds invoice residual."))

        # Get a cash/bank journal
        if payment_method == "cash":
            journal = env["account.journal"].search([
                ("type", "=", "cash"),
                ("company_id", "=", env.company.id),
            ], limit=1)
        else:
            journal = env["account.journal"].search([
                ("type", "=", "bank"),
                ("company_id", "=", env.company.id),
            ], limit=1)
        
        if not journal:
            journal = env["account.journal"].search([
                ("type", "in", ["bank", "cash"]),
                ("company_id", "=", env.company.id),
            ], limit=1)
        
        if not journal:
            raise ValidationError(_("No cash or bank journal found."))
        
        payment_logger.info(f"Journal selected: ID={journal.id}, Name={journal.name}")

        # Get or create payment method line
        payment_method_line = env["account.payment.method.line"].search([
            ("journal_id", "=", journal.id),
            ("payment_type", "=", "inbound"),
        ], limit=1)
        
        if not payment_method_line:
            manual_method = env.ref("account.account_payment_method_manual_in", raise_if_not_found=False)
            if manual_method:
                # Get the company's transfer account or default account
                account_id = journal.company_id.transfer_account_id.id or journal.default_account_id.id
                payment_method_line = env["account.payment.method.line"].create({
                    "name": "Manual",
                    "journal_id": journal.id,
                    "payment_method_id": manual_method.id,
                    "payment_account_id": account_id,
                })
                payment_logger.info(f"Created payment method line: ID={payment_method_line.id}")

        # Create payment
        payment_vals = {
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": invoice.partner_id.id,
            "amount": float(amount),
            "date": fields.Date.from_string(payment_date) if payment_date else fields.Date.today(),
            "journal_id": journal.id,
            "payment_method_line_id": payment_method_line.id if payment_method_line else False,
            "name": reference or f"Payment for {invoice.name}",
        }
        
        payment_logger.info(f"Creating payment: {payment_vals}")
        payment = env["account.payment"].sudo().create(payment_vals)
        payment_logger.info(f"Payment created: ID={payment.id}, State={payment.state}")

        # CRITICAL: Force move creation by posting and syncing
        payment.action_post()
        payment_logger.info(f"Payment posted: State={payment.state}")

        # Force refresh to get move_id
        payment.invalidate_recordset()
        payment = env["account.payment"].browse(payment.id)
        payment_logger.info(f"After refresh - Move ID: {payment.move_id.id if payment.move_id else 'None'}")

        # If still no move, create journal entry manually
        if not payment.move_id:
            payment_logger.warning("No move_id, creating journal entry manually...")
            
            # Get receivable account from invoice
            receivable_line = invoice.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable'
            )[:1]
            receivable_account = receivable_line.account_id if receivable_line else False
            
            if not receivable_account:
                raise ValidationError(_("Could not find receivable account for invoice."))
            
            # Get the payment's liquidity account from journal
            liquidity_account = journal.default_account_id or journal.company_id.transfer_account_id
            
            # Create journal entry manually
            move_vals = {
                "journal_id": journal.id,
                "date": payment.date,
                "ref": payment.name,
                "line_ids": [
                    (0, 0, {
                        "name": payment.name,
                        "account_id": liquidity_account.id,
                        "debit": payment.amount,
                        "credit": 0,
                        "partner_id": payment.partner_id.id,
                    }),
                    (0, 0, {
                        "name": payment.name,
                        "account_id": receivable_account.id,
                        "debit": 0,
                        "credit": payment.amount,
                        "partner_id": payment.partner_id.id,
                    }),
                ],
            }
            
            move = env["account.move"].create(move_vals)
            move.action_post()
            payment.write({"move_id": move.id})
            payment_logger.info(f"Manual move created: ID={move.id}")

        # Now reconcile
        if payment.move_id:
            payment_logger.info(f"Payment move found: ID={payment.move_id.id}")
            
            payment_lines = payment.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable' and l.credit > 0
            )
            invoice_lines = invoice.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable' and l.debit > 0 and not l.reconciled
            )
            
            payment_logger.info(f"Payment lines: {len(payment_lines)}, Invoice lines: {len(invoice_lines)}")
            
            if payment_lines and invoice_lines:
                (payment_lines + invoice_lines).reconcile()
                payment_logger.info("SUCCESS: Payment reconciled!")
            else:
                payment_logger.warning("No lines to reconcile")
        else:
            payment_logger.error("Still no move_id after manual creation!")

        invoice.invalidate_recordset()
        payment_logger.info(f"Final invoice state: {invoice.payment_state}, Residual: {invoice.amount_residual}")

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
        }, message="Payment registered.")

    # Keep the other endpoint methods (get_payment_status, etc.)
    @http.route("/api/v1/invoices/<int:invoice_id>/payment-status", auth="public", methods=["GET"], type="http", csrf=False)
    def get_payment_status(self, invoice_id, **kwargs):
        return self._handle_route(lambda env: self._get_payment_status(env, invoice_id))

    def _get_payment_status(self, env, invoice_id):
        invoice = env["account.move"].browse(invoice_id)
        if not invoice.exists():
            raise MissingError(_("Invoice #%s not found.") % invoice_id)
        payments = env["account.payment"].search([("invoice_ids", "in", invoice.id), ("state", "=", "posted")])
        return self._success({
            "invoice_id": invoice.id,
            "invoice_name": invoice.name,
            "amount_total": invoice.amount_total,
            "amount_residual": invoice.amount_residual,
            "payment_state": invoice.payment_state,
            "payments": [{"id": p.id, "name": p.name, "amount": p.amount, "date": str(p.date), "journal_name": p.journal_id.name} for p in payments],
        })

    @http.route("/api/v1/payment-methods", auth="public", methods=["GET"], type="http", csrf=False)
    def get_payment_methods(self, **kwargs):
        return self._handle_route(lambda env: self._list_payment_methods(env))

    def _list_payment_methods(self, env):
        payment_methods = env["account.payment.method"].search([])
        items = [{"id": pm.id, "name": pm.name, "code": pm.code if hasattr(pm, 'code') else "", "payment_type": pm.payment_type if hasattr(pm, 'payment_type') else "inbound"} for pm in payment_methods]
        return self._success({"items": items, "total": len(items)})

    @http.route("/api/v1/payments/overdue", auth="public", methods=["GET"], type="http", csrf=False)
    def get_overdue_payments(self, limit=100, **kwargs):
        return self._handle_route(lambda env: self._get_overdue(env, limit))

    def _get_overdue(self, env, limit):
        try:
            limit = min(int(limit), 500)
        except (ValueError, TypeError):
            limit = 100
        from datetime import date
        today = date.today()
        overdue = env["account.move"].search_read(
            domain=[("move_type", "=", "out_invoice"), ("state", "=", "posted"), ("payment_state", "!=", "paid"), ("invoice_date_due", "<", today)],
            fields=["id", "name", "partner_id", "amount_total", "amount_residual", "invoice_date_due", "payment_state"],
            limit=limit, order="invoice_date_due asc",
        )
        total_overdue = sum(inv.get("amount_residual", 0) for inv in overdue)
        return self._success({"items": overdue, "total": len(overdue), "total_amount_overdue": total_overdue})
