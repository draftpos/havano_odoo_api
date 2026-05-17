# controllers/sales.py
from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError
from .common import HavanoApiControllerMixin
from .helpers import safe_field_get, safe_field_exists, idempotent_check, safe_search, log_and_raise

import logging
_logger = logging.getLogger(__name__)


class HavanoSalesController(HavanoApiControllerMixin, http.Controller):
    """
    POS Sales Controller - Handles sales only (NO PAYMENTS)
    Creates: Quotation -> Sales Order -> Invoice -> Delivery Order
    """

    @http.route("/api/v1/sales", auth="public", methods=["POST"], type="http", csrf=False)
    def process_pos_sale(self, **kwargs):
        return self._handle_route(lambda env: self._process_sale(env))

    def _process_sale(self, env):
        """Main processing logic - creates sale, invoice, delivery (NO PAYMENTS)"""
        data = self._parse_json_data()

        # ================================================================
        # 1. VALIDATION
        # ================================================================
        pos_reference = data.get("pos_reference")
        lines = data.get("lines", [])

        if not pos_reference:
            log_and_raise("pos_reference is required.", 'error')
        if not lines:
            log_and_raise("At least one sale line is required.", 'error')

        _logger.info("Processing POS sale: %s with %s lines", pos_reference, len(lines))

        # ================================================================
        # 2. IDEMPOTENCY CHECK (Prevent duplicate processing)
        # ================================================================
        existing_order = idempotent_check(
            env["sale.order"], "client_order_ref", pos_reference, pos_reference, "Sales Order"
        )
        if existing_order:
            return self._success({
                "sale_order_id": existing_order.id,
                "sale_order_name": existing_order.name,
                "sale_order_state": existing_order.state,
                "already_processed": True,
            }, message="Sale already processed.", status=200)

        # ================================================================
        # 3. GET USER WORKFLOW CONFIGURATION
        # ================================================================
        user = env.user
        if hasattr(user, 'get_pos_workflow_config'):
            workflow = user.get_pos_workflow_config()
        else:
            workflow = {
                'auto_confirm_sale': False,
                'auto_create_invoice': False,
                'auto_post_invoice': False,
                'auto_validate_delivery': False,
            }
        _logger.info("User %s workflow config: %s", user.name, workflow)

        # ================================================================
        # 4. RESOLVE CUSTOMER
        # ================================================================
        partner = self._resolve_partner(env, data)
        if not partner:
            log_and_raise("Could not resolve customer.", 'error')

        # ================================================================
        # 5. CREATE SALES ORDER (draft)
        # ================================================================
        sale_order = self._create_sales_order(env, partner, pos_reference, lines, data)
        sale_order_state = "draft"
        sale_order_confirmed = False

        # ================================================================
        # 6. CONDITIONALLY CONFIRM SALES ORDER
        # ================================================================
        if workflow.get('auto_confirm_sale', False):
            try:
                sale_order.action_confirm()
                sale_order_state = "sale"
                sale_order_confirmed = True
                _logger.info("Sales order auto-confirmed: %s (user: %s)", sale_order.name, user.name)
            except Exception as e:
                _logger.error(f"Failed to confirm sales order {sale_order.name}: {str(e)}")
        else:
            _logger.info("Sales order left as quotation: %s (user: %s)", sale_order.name, user.name)

        # ================================================================
        # 7. CONDITIONALLY CREATE INVOICE
        # ================================================================
        invoice = None
        invoice_created = False
        invoice_posted = False

        if workflow.get('auto_create_invoice', False) and sale_order_confirmed:
            try:
                invoice = self._create_invoice_from_sale_order(env, sale_order)
                invoice_created = True
                _logger.info("Invoice created: %s", invoice.name if invoice else 'None')

                if invoice and workflow.get('auto_post_invoice', False):
                    invoice.action_post()
                    invoice_posted = True
                    _logger.info("Invoice auto-posted: %s", invoice.name)
            except Exception as e:
                _logger.error(f"Failed to create invoice: {str(e)}")

        # ================================================================
        # 8. CONDITIONALLY CREATE DELIVERY ORDER
        # ================================================================
        picking = None
        delivery_validated = False

        if workflow.get('auto_validate_delivery', False) and sale_order_confirmed:
            try:
                picking = self._create_delivery_order(env, partner, sale_order, lines, pos_reference, validate=True)
                delivery_validated = picking is not None
                _logger.info("Delivery order validated: %s", picking.name if picking else 'None')
            except Exception as e:
                _logger.error(f"Failed to create validated delivery order: {str(e)}")
        elif sale_order_confirmed:
            try:
                picking = self._create_delivery_order(env, partner, sale_order, lines, pos_reference, validate=False)
                _logger.info("Delivery order created (not validated): %s", picking.name if picking else 'None')
            except Exception as e:
                _logger.error(f"Failed to create delivery order: {str(e)}")

        # ================================================================
        # 9. REFRESH RECORD STATES
        # ================================================================
        if invoice:
            invoice.invalidate_recordset()
        if sale_order:
            sale_order.invalidate_recordset()
        if picking:
            picking.invalidate_recordset()

        # ================================================================
        # 10. RETURN SUCCESS (NO PAYMENT INFO)
        # ================================================================
        return self._success({
            "sale_order_id": sale_order.id,
            "sale_order_name": sale_order.name,
            "sale_order_state": sale_order_state,
            "invoice_id": invoice.id if invoice else None,
            "invoice_name": invoice.name if invoice else None,
            "invoice_state": invoice.state if invoice else None,
            "invoice_amount_total": invoice.amount_total if invoice else None,
            "invoice_amount_residual": invoice.amount_residual if invoice else None,
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
            }
        }, message="Sale processed successfully. Ready for payment.", status=201)

    # ================================================================
    # HELPER METHODS
    # ================================================================

    def _resolve_partner(self, env, data):
        """Resolve customer - creates if doesn't exist."""
        partner_model = env["res.partner"].sudo()
        customer_id = data.get("customer_id")
        customer_name = data.get("customer_name", "").strip()
        customer_email = data.get("customer_email", "").strip()
        customer_phone = data.get("customer_phone", "").strip()

        # Try by ID
        if customer_id:
            partner = partner_model.browse(int(customer_id))
            if partner.exists():
                _logger.info("Using existing customer by ID: %s (id=%s)", partner.name, partner.id)
                return partner

        # Try by email
        if customer_email:
            partner = partner_model.search([("email", "=", customer_email.lower())], limit=1)
            if partner:
                _logger.info("Found existing customer by email: %s (id=%s)", partner.name, partner.id)
                return partner

        # Try by phone
        if customer_phone:
            partner = partner_model.search([("phone", "=", customer_phone)], limit=1)
            if partner:
                _logger.info("Found existing customer by phone: %s (id=%s)", partner.name, partner.id)
                return partner

        # Try by name
        if customer_name:
            partner = partner_model.search([("name", "=", customer_name)], limit=1)
            if partner:
                _logger.info("Found existing customer by name: %s (id=%s)", partner.name, partner.id)
                return partner

            # Create new customer
            partner = partner_model.create({
                "name": customer_name,
                "email": customer_email,
                "phone": customer_phone,
                "customer_rank": 1,
            })
            _logger.info("Created new customer: %s (id=%s)", partner.name, partner.id)
            return partner

        # Default customer
        partner = partner_model.search([("name", "=", "POS Customer")], limit=1)
        if not partner:
            partner = partner_model.create({"name": "POS Customer", "customer_rank": 1})
            _logger.info("Created default POS Customer (id=%s)", partner.id)

        return partner

    def _create_sales_order(self, env, partner, pos_reference, lines, data):
        """Create a draft sales order."""
        SaleOrder = env["sale.order"].sudo()
        pricelist = partner.property_product_pricelist

        order_lines = []
        for line_data in lines:
            product_id = line_data.get("product_id")
            quantity = float(line_data.get("quantity", 1))

            product = env["product.product"].sudo().browse(int(product_id))
            if not product.exists():
                _logger.warning(f"Product {product_id} not found, skipping")
                continue

            # Get price
            if line_data.get("price_unit") is not None:
                price_unit = float(line_data.get("price_unit"))
            elif pricelist:
                price_unit = pricelist._get_product_price(product, quantity, product.uom_id, fields.Date.today())
            else:
                price_unit = product.lst_price

            # Get taxes
            tax_ids = []
            if hasattr(product, 'taxes_id'):
                tax_ids = product.taxes_id.filtered(lambda t: getattr(t, 'type_tax_use', 'sale') == "sale").ids

            order_lines.append((0, 0, {
                "product_id": product.id,
                "name": line_data.get("name") or product.display_name,
                "product_uom_qty": quantity,
                "product_uom_id": product.uom_id.id,
                "price_unit": price_unit,
                "tax_ids": [(6, 0, tax_ids)] if tax_ids else False,
            }))

        if not order_lines:
            log_and_raise("No valid products found in sale lines.", 'error')

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
        _logger.info("Sales order created: %s (id=%s)", sale_order.name, sale_order.id)

        return sale_order

    def _create_invoice_from_sale_order(self, env, sale_order):
        """Create invoice from confirmed sales order."""
        invoice = sale_order._create_invoices()
        if not invoice:
            raise ValidationError(_("Failed to create invoice from sales order."))
        _logger.info("Invoice created from sales order: %s (id=%s)", invoice.name, invoice.id)
        return invoice

    def _create_delivery_order(self, env, partner, order, lines, pos_reference, validate=False):
        """Create delivery order, optionally validate it."""
        try:
            # Check if stock module is installed
            if 'stock.warehouse' not in env:
                _logger.warning("Stock module not installed, skipping delivery order")
                return None

            warehouse = safe_search(env, "stock.warehouse", [], limit=1)
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
                    continue

                # Only create delivery for stockable products
                if safe_field_get(product, 'type') != "consu":
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

            if validate:
                for move in picking.move_ids:
                    if move.product_uom_qty > 0:
                        move.quantity = move.product_uom_qty
                picking.button_validate()
                _logger.info("Delivery order validated: %s", picking.name)
            else:
                _logger.info("Delivery order created (not validated): %s", picking.name)

            return picking

        except Exception as e:
            _logger.error("Failed to create delivery order: %s", str(e))
            return None