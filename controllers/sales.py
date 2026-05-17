# controllers/sales.py
from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError
from .common import HavanoApiControllerMixin

import logging
_logger = logging.getLogger(__name__)


class HavanoSalesController(HavanoApiControllerMixin, http.Controller):
    """
    POS Sales Controller
    
    Receives finalized POS transactions and records them in Odoo.
    Creates Sales Orders, Invoices, Deliveries, and Payments based on user workflow preferences.
    """
    
    @http.route("/api/v1/sales", auth="public", methods=["POST"], type="http", csrf=False)
    def process_pos_sale(self, **kwargs):
        return self._handle_route(lambda env: self._process_sale(env))
    
    def _process_sale(self, env):
        """Main processing logic - respects user workflow preferences"""
        data = self._parse_json_data()
        
        # ================================================================
        # 1. VALIDATION
        # ================================================================
        pos_reference = data.get("pos_reference")
        lines = data.get("lines", [])
        payments = data.get("payments", [])
        
        if not pos_reference:
            raise ValidationError(_("pos_reference is required."))
        
        if not lines:
            raise ValidationError(_("At least one sale line is required."))
        
        _logger.info("Processing POS sale: %s with %s lines and %s payments", 
                    pos_reference, len(lines), len(payments))
        
        # ================================================================
        # 2. DUPLICATE PROTECTION
        # ================================================================
        existing_sale_order = env["sale.order"].sudo().search([
            ("client_order_ref", "=", pos_reference),
        ], limit=1)
        
        if existing_sale_order:
            _logger.info("Duplicate POS sale detected: %s (sale order #%s)", 
                        pos_reference, existing_sale_order.name)
            return self._success({
                "sale_order_id": existing_sale_order.id,
                "sale_order_name": existing_sale_order.name,
                "sale_order_state": existing_sale_order.state,
                "invoice_id": existing_sale_order.invoice_ids.ids[0] if existing_sale_order.invoice_ids else None,
                "amount_total": existing_sale_order.amount_total,
                "state": existing_sale_order.state,
                "duplicate": True,
            }, message=_("Sale already processed."))
        
        # ================================================================
        # 3. GET USER WORKFLOW CONFIGURATION
        # ================================================================
        user = env.user
        workflow = user.get_pos_workflow_config()
        _logger.info("User %s workflow config: %s", user.name, workflow)
        
        # ================================================================
        # 4. RESOLVE CUSTOMER
        # ================================================================
        partner = self._resolve_partner(env, data)
        
        # ================================================================
        # 5. CREATE SALES ORDER (always in draft)
        # ================================================================
        sale_order = self._create_sales_order(env, partner, pos_reference, lines, data)
        sale_order_state = sale_order.state
        sale_order_confirmed = False
        
        # ================================================================
        # 6. CONDITIONALLY CONFIRM SALES ORDER
        # ================================================================
        if workflow['auto_confirm_sale']:
            if sale_order.state == 'draft':
                sale_order.action_confirm()
                sale_order_state = sale_order.state
                sale_order_confirmed = True
                _logger.info("Sales order auto-confirmed: %s (user: %s)", sale_order.name, user.name)
        else:
            _logger.info("Sales order left as quotation: %s (user: %s)", sale_order.name, user.name)
        
        # ================================================================
        # 7. INITIALIZE VARIABLES FOR DOWNSTREAM PROCESSING
        # ================================================================
        invoice = None
        picking = None
        invoice_created = False
        invoice_posted = False
        delivery_validated = False
        payment_registered = False
        
        # The order object to use for invoice/delivery creation
        order_for_downstream = sale_order if sale_order_confirmed else None
        
        # ================================================================
        # 8. CONDITIONALLY CREATE AND POST INVOICE
        # ================================================================
        if workflow['auto_create_invoice'] and order_for_downstream:
            invoice = self._create_invoice_from_sale_order(env, order_for_downstream, lines, data)
            invoice_created = True
            
            if invoice and workflow['auto_post_invoice']:
                invoice.action_post()
                invoice_posted = True
                _logger.info("Invoice auto-posted: %s (user: %s)", invoice.name, user.name)
            elif invoice:
                _logger.info("Invoice created but not posted: %s (user: %s)", invoice.name, user.name)
        elif workflow['auto_create_invoice'] and not order_for_downstream:
            _logger.info("Invoice creation skipped - sales order not confirmed (user: %s)", user.name)
        
        # ================================================================
        # 9. CONDITIONALLY CREATE AND VALIDATE DELIVERY ORDER
        # ================================================================
        if workflow['auto_validate_delivery'] and order_for_downstream:
            picking = self._create_and_validate_delivery_order(env, partner, invoice or order_for_downstream, lines, pos_reference)
            delivery_validated = picking is not None
            _logger.info("Delivery order validated: %s (user: %s)", picking.name if picking else 'None', user.name)
        elif workflow['auto_create_invoice'] and order_for_downstream:
            # Create delivery but don't validate
            picking = self._create_delivery_order_only(env, partner, order_for_downstream, lines, pos_reference)
            _logger.info("Delivery order created but not validated: %s (user: %s)", picking.name if picking else 'None', user.name)
        
        # ================================================================
        # 10. CONDITIONALLY REGISTER PAYMENTS
        # ================================================================
        if workflow['auto_register_payment'] and invoice and invoice.state == 'posted':
            for payment_data in payments:
                self._register_and_reconcile_payment(env, invoice, payment_data, pos_reference)
            payment_registered = True
            _logger.info("Payments auto-registered for invoice: %s (user: %s)", invoice.name, user.name)
        elif payments and invoice:
            _logger.info("Payments received but not auto-registered (user: %s)", user.name)
        elif payments and not invoice:
            _logger.info("Payments received but no invoice created (user: %s)", user.name)
        
        # ================================================================
        # 11. REFRESH RECORD STATES
        # ================================================================
        if invoice:
            invoice.invalidate_recordset()
        if sale_order:
            sale_order.invalidate_recordset()
        if picking:
            picking.invalidate_recordset()
        
        # ================================================================
        # 12. RETURN SUCCESS WITH WORKFLOW INFO
        # ================================================================
        return self._success({
            "sale_order_id": sale_order.id,
            "sale_order_name": sale_order.name,
            "sale_order_state": sale_order_state,
            "invoice_id": invoice.id if invoice else None,
            "invoice_name": invoice.name if invoice else None,
            "invoice_state": invoice.state if invoice else None,
            "delivery_order_id": picking.id if picking else None,
            "delivery_order_name": picking.name if picking else None,
            "delivery_order_state": picking.state if picking else None,
            "amount_total": invoice.amount_total if invoice else sale_order.amount_total,
            "partner_id": partner.id,
            "partner_name": partner.name,
            "workflow_applied": {
                "order_confirmed": sale_order_confirmed,
                "invoice_created": invoice_created,
                "invoice_posted": invoice_posted,
                "delivery_validated": delivery_validated,
                "payment_registered": payment_registered,
            }
        }, message=_("Sale processed successfully."), status=201)
    
    # ================================================================
    # HELPER METHODS
    # ================================================================
    
    def _resolve_partner(self, env, data):
        """Resolve the customer for the sale."""
        partner_model = env["res.partner"].sudo()
        
        customer_id = data.get("customer_id")
        if customer_id:
            partner = partner_model.browse(int(customer_id))
            if partner.exists():
                _logger.info("Using existing customer: %s (id=%s)", partner.name, partner.id)
                return partner
        
        customer_name = data.get("customer_name", "").strip()
        if customer_name:
            partner = partner_model.search([
                ("name", "=", customer_name),
                ("customer_rank", ">", 0),
            ], limit=1)
            
            if partner:
                _logger.info("Found walk-in customer: %s (id=%s)", partner.name, partner.id)
                return partner
            
            partner = partner_model.create({
                "name": customer_name,
                "customer_rank": 1,
            })
            _logger.info("Created walk-in customer: %s (id=%s)", partner.name, partner.id)
            return partner
        
        partner = partner_model.search([
            ("name", "=", "POS Customer"),
        ], limit=1)
        
        if not partner:
            partner = partner_model.create({
                "name": "POS Customer",
                "customer_rank": 1,
            })
            _logger.info("Created default POS Customer (id=%s)", partner.id)
        
        return partner
    
    def _create_sales_order(self, env, partner, pos_reference, lines, data):
        """Create a sales order from the POS sale."""
        SaleOrder = env["sale.order"].sudo()
        
        pricelist = partner.property_product_pricelist
        
        order_lines = []
        for line_data in lines:
            product_id = line_data.get("product_id")
            quantity = float(line_data.get("quantity", 1))
            
            product = env["product.product"].sudo().browse(int(product_id))
            if not product.exists():
                template = env["product.template"].sudo().browse(int(product_id))
                if template.exists() and template.product_variant_ids:
                    product = template.product_variant_ids[0]
                else:
                    raise ValidationError(_("Product #%s not found.") % product_id)
            
            if line_data.get("price_unit") is not None:
                price_unit = float(line_data.get("price_unit"))
            elif pricelist:
                price_unit = pricelist._get_product_price(
                    product=product,
                    quantity=quantity,
                    uom=product.uom_id,
                    date=fields.Date.today(),
                )
            else:
                price_unit = product.lst_price
            
            tax_ids = product.taxes_id.filtered(
                lambda t: t.type_tax_use == "sale"
            ).ids
            
            order_lines.append((0, 0, {
                "product_id": product.id,
                "name": line_data.get("name") or product.display_name,
                "product_uom_qty": quantity,
                "product_uom_id": product.uom_id.id,
                "price_unit": price_unit,
                "tax_ids": [(6, 0, tax_ids)],
            }))
        
        so_vals = {
            "partner_id": partner.id,
            "partner_invoice_id": partner.id,
            "partner_shipping_id": partner.id,
            "pricelist_id": pricelist.id if pricelist else False,
            "client_order_ref": pos_reference,
            "origin": pos_reference,
            "date_order": data.get("date_order") or fields.Datetime.now(),
            "order_line": order_lines,
            "note": data.get("note", ""),
        }
        
        sale_order = SaleOrder.create(so_vals)
        _logger.info("Sales order created: %s (id=%s) for partner %s", 
                    sale_order.name, sale_order.id, partner.name)
        
        return sale_order
    
    def _create_invoice_from_sale_order(self, env, sale_order, lines, data):
        """Create an invoice from the sales order."""
        invoice = sale_order._create_invoices()
        
        if not invoice:
            raise ValidationError(_("Failed to create invoice from sales order."))
        
        _logger.info("Invoice created from sales order: %s (id=%s)", invoice.name, invoice.id)
        return invoice
    
    def _create_and_validate_delivery_order(self, env, partner, order, lines, pos_reference):
        """Create, confirm, assign, and validate a delivery order."""
        try:
            warehouse = env["stock.warehouse"].sudo().search([], limit=1)
            if not warehouse:
                _logger.warning("No warehouse found, cannot create delivery order")
                return None
            
            picking_type = warehouse.out_type_id
            if not picking_type:
                _logger.warning("No out_type_id found for warehouse %s", warehouse.name)
                return None
            
            move_lines = []
            for line_data in lines:
                product_id = line_data.get("product_id")
                quantity = float(line_data.get("quantity", 1))
                
                product = env["product.product"].sudo().browse(int(product_id))
                if not product.exists():
                    template = env["product.template"].sudo().browse(int(product_id))
                    if template.exists() and template.product_variant_ids:
                        product = template.product_variant_ids[0]
                    else:
                        continue
                
                if product.type != "consu":
                    continue
                
                source_location = picking_type.default_location_src_id
                dest_location = picking_type.default_location_dest_id or warehouse.lot_stock_id
                
                move_lines.append((0, 0, {
                    "product_id": product.id,
                    "product_uom_qty": quantity,
                    "product_uom": product.uom_id.id,
                    "location_id": source_location.id,
                    "location_dest_id": dest_location.id,
                }))
            
            if not move_lines:
                return None
            
            picking = env["stock.picking"].sudo().create({
                "partner_id": partner.id,
                "picking_type_id": picking_type.id,
                "location_id": picking_type.default_location_src_id.id,
                "location_dest_id": picking_type.default_location_dest_id.id or warehouse.lot_stock_id.id,
                "origin": pos_reference,
                "move_ids": move_lines,
            })
            
            _logger.info("Delivery order created: %s", picking.name)
            
            picking.action_confirm()
            picking.action_assign()
            
            for move in picking.move_ids:
                if move.product_uom_qty > 0:
                    move.quantity = move.product_uom_qty
            
            picking.button_validate()
            _logger.info("Delivery order validated: %s", picking.name)
            
            return picking
            
        except Exception as e:
            _logger.error("Failed to create/validate delivery order: %s", str(e))
            return None
    
    def _create_delivery_order_only(self, env, partner, order, lines, pos_reference):
        """Create delivery order without validation."""
        try:
            warehouse = env["stock.warehouse"].sudo().search([], limit=1)
            if not warehouse:
                return None
            
            picking_type = warehouse.out_type_id
            if not picking_type:
                return None
            
            move_lines = []
            for line_data in lines:
                product_id = line_data.get("product_id")
                quantity = float(line_data.get("quantity", 1))
                
                product = env["product.product"].sudo().browse(int(product_id))
                if not product.exists():
                    template = env["product.template"].sudo().browse(int(product_id))
                    if template.exists() and template.product_variant_ids:
                        product = template.product_variant_ids[0]
                    else:
                        continue
                
                if product.type != "consu":
                    continue
                
                source_location = picking_type.default_location_src_id
                dest_location = picking_type.default_location_dest_id or warehouse.lot_stock_id
                
                move_lines.append((0, 0, {
                    "product_id": product.id,
                    "product_uom_qty": quantity,
                    "product_uom": product.uom_id.id,
                    "location_id": source_location.id,
                    "location_dest_id": dest_location.id,
                }))
            
            if not move_lines:
                return None
            
            picking = env["stock.picking"].sudo().create({
                "partner_id": partner.id,
                "picking_type_id": picking_type.id,
                "location_id": picking_type.default_location_src_id.id,
                "location_dest_id": picking_type.default_location_dest_id.id or warehouse.lot_stock_id.id,
                "origin": pos_reference,
                "move_ids": move_lines,
            })
            
            picking.action_confirm()
            picking.action_assign()
            # NOT validating - leave for manual processing
            
            _logger.info("Delivery order created (not validated): %s", picking.name)
            return picking
            
        except Exception as e:
            _logger.error("Failed to create delivery order: %s", str(e))
            return None
    
    def _register_and_reconcile_payment(self, env, invoice, payment_data, pos_reference):
        """Create a payment, post it, and fully reconcile with the invoice."""
        Payment = env["account.payment"].sudo()
        
        amount = float(payment_data.get("amount", 0))
        if amount <= 0:
            _logger.warning("Skipping payment with amount <= 0: %s", amount)
            return
        
        method = payment_data.get("method", "cash").lower()
        
        journal = self._get_payment_journal(env, method)
        payment_method_line = self._get_payment_method_line(env, journal, method)
        
        payment = Payment.create({
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": invoice.partner_id.id,
            "amount": amount,
            "journal_id": journal.id,
            "payment_method_line_id": payment_method_line.id if payment_method_line else False,
            "date": fields.Date.today(),
            "memo": f"{pos_reference} - {method.title()} Payment",
            "invoice_ids": [(6, 0, [invoice.id])],
        })
        
        payment.action_post()
        payment.invalidate_recordset()
        _logger.info("Payment posted: id=%s, amount=%s, method=%s", payment.id, amount, method)
        
        if payment.move_id:
            self._reconcile_payment_with_invoice(env, invoice, payment)
        else:
            self._reconcile_payment_direct(env, invoice, payment)
    
    def _reconcile_payment_with_invoice(self, env, invoice, payment):
        """Reconcile payment with invoice using move lines."""
        try:
            payment_move = payment.move_id
            if not payment_move:
                return
            
            payment_lines = payment_move.line_ids.filtered(
                lambda line: line.account_id.account_type in ('asset_receivable', 'liability_payable')
                and not line.reconciled
            )
            invoice_lines = invoice.line_ids.filtered(
                lambda line: line.account_id.account_type in ('asset_receivable', 'liability_payable')
                and not line.reconciled
            )
            
            if payment_lines and invoice_lines:
                (payment_lines + invoice_lines).reconcile()
                invoice.invalidate_recordset(['payment_state', 'amount_residual'])
                _logger.info("Payment reconciled with invoice %s", invoice.name)
                
        except Exception as e:
            _logger.error("Reconciliation error: %s", str(e))
    
    def _reconcile_payment_direct(self, env, invoice, payment):
        """Directly reconcile payment with invoice using account.move."""
        try:
            AccountMove = env["account.move"].sudo()
            
            receivable_account = invoice.partner_id.property_account_receivable_id
            if not receivable_account:
                receivable_account = env['account.account'].search([
                    ('account_type', '=', 'asset_receivable'),
                    ('company_id', '=', env.company.id),
                ], limit=1)
            
            journal = self._get_payment_journal(env, "cash")
            
            move_vals = {
                'move_type': 'entry',
                'journal_id': journal.id,
                'date': fields.Date.today(),
                'ref': payment.memo,
                'line_ids': [
                    (0, 0, {
                        'name': f'Payment for {invoice.name}',
                        'account_id': journal.default_account_id.id,
                        'debit': payment.amount,
                        'credit': 0,
                        'partner_id': invoice.partner_id.id,
                    }),
                    (0, 0, {
                        'name': f'Payment for {invoice.name}',
                        'account_id': receivable_account.id,
                        'debit': 0,
                        'credit': payment.amount,
                        'partner_id': invoice.partner_id.id,
                    }),
                ],
            }
            
            move = AccountMove.create(move_vals)
            move.action_post()
            
            move_lines = move.line_ids.filtered(lambda l: l.account_id == receivable_account)
            invoice_lines = invoice.line_ids.filtered(lambda l: l.account_id == receivable_account)
            
            if move_lines and invoice_lines:
                (move_lines + invoice_lines).reconcile()
                invoice.invalidate_recordset(['payment_state', 'amount_residual'])
                _logger.info("Payment reconciled via direct move: %s", move.name)
                
        except Exception as e:
            _logger.error("Direct reconciliation failed: %s", str(e))
    
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
    
    def _get_payment_method_line(self, env, journal, method):
        """Find appropriate payment method line for the journal."""
        PaymentMethodLine = env["account.payment.method.line"].sudo()
        
        lines = journal.inbound_payment_method_line_ids
        
        if not lines:
            payment_method = env.ref(
                "account.account_payment_method_manual_in", 
                raise_if_not_found=False
            )
            if payment_method:
                lines = PaymentMethodLine.search([
                    ("payment_method_id", "=", payment_method.id),
                    ("journal_id", "=", journal.id),
                ], limit=1)
        
        return lines[:1] if lines else None