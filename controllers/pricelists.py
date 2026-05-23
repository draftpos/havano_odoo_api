from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import MissingError, ValidationError
from .common import HavanoApiControllerMixin

import logging
_logger = logging.getLogger(__name__)


class HavanoPricelistsController(HavanoApiControllerMixin, http.Controller):
    
    def _serialize_pricelist(self, pricelist, include_rules=False):
        """Convert product.pricelist to API response dict."""
        data = {
            "id": pricelist.id,
            "name": pricelist.name,
            "display_name": pricelist.display_name,
            "active": pricelist.active,
            "sequence": pricelist.sequence,
            "currency_id": pricelist.currency_id.id,
            "currency_name": pricelist.currency_id.name,
            "currency_symbol": pricelist.currency_id.symbol,
            "company_id": pricelist.company_id.id,
            "company_name": pricelist.company_id.name,
            "country_group_ids": [{
                "id": g.id,
                "name": g.name,
            } for g in pricelist.country_group_ids],
        }
        
        if include_rules:
            data["rules"] = self._serialize_rules(pricelist.item_ids)
        
        return data
    
    def _serialize_rules(self, rules):
        """Convert pricelist rules to API response."""
        return [{
            "id": rule.id,
            "name": rule.name,
            "price": rule.price,
            "rule_tip": rule.rule_tip,
            "applied_on": rule.applied_on,
            "min_quantity": rule.min_quantity,
            "compute_price": rule.compute_price,
            "fixed_price": rule.fixed_price,
            "percent_price": rule.percent_price,
            "price_discount": rule.price_discount,
            "price_surcharge": rule.price_surcharge,
            "price_round": rule.price_round,
            "price_markup": rule.price_markup,
            "price_min_margin": rule.price_min_margin,
            "price_max_margin": rule.price_max_margin,
            "base": rule.base,
            "base_pricelist_id": rule.base_pricelist_id.id if rule.base_pricelist_id else None,
            "base_pricelist_name": rule.base_pricelist_id.name if rule.base_pricelist_id else None,
            "date_start": str(rule.date_start) if rule.date_start else None,
            "date_end": str(rule.date_end) if rule.date_end else None,
            # Product/category targets
            "product_id": rule.product_id.id if rule.product_id else None,
            "product_name": rule.product_id.display_name if rule.product_id else None,
            "product_tmpl_id": rule.product_tmpl_id.id if rule.product_tmpl_id else None,
            "product_tmpl_name": rule.product_tmpl_id.name if rule.product_tmpl_id else None,
            "categ_id": rule.categ_id.id if rule.categ_id else None,
            "categ_name": rule.categ_id.name if rule.categ_id else None,
        } for rule in rules]
    
    # ================================================================
    # PRICELIST LISTING ENDPOINTS
    # ================================================================
    
    @http.route("/api/v1/pricelists", auth="public", methods=["GET"], type="http", csrf=False)
    def list_pricelists(self, active_only=True, include_rules=False, limit=100, offset=0, **kwargs):
        """GET /api/v1/pricelists - List all pricelists
        
        Query params:
        - active_only: bool (default True)
        - include_rules: bool (default False) - include full rule details
        - limit: int (default 100, max 500)
        - offset: int (default 0)
        """
        return self._handle_route(lambda env: self._list_pricelists(
            env, active_only, include_rules, limit, offset
        ))
    
    def _list_pricelists(self, env, active_only, include_rules, limit, offset):
        try:
            limit = min(int(limit), 500)
            offset = int(offset) if offset else 0
            active_only = str(active_only).lower() in ('true', '1', 'yes', 'on')
            include_rules = str(include_rules).lower() in ('true', '1', 'yes', 'on')
        except (ValueError, TypeError):
            limit, offset = 100, 0
        
        domain = []
        if active_only:
            domain.append(("active", "=", True))
        
        pricelists = env["product.pricelist"].search(
            domain, limit=limit, offset=offset, order="sequence, id"
        )
        
        items = [self._serialize_pricelist(p, include_rules) for p in pricelists]
        total = env["product.pricelist"].search_count(domain)
        
        return self._success({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        })
    
    # ================================================================
    # SINGLE PRICELIST ENDPOINTS
    # ================================================================
    
    @http.route("/api/v1/pricelists/<int:pricelist_id>", auth="public", methods=["GET"], type="http", csrf=False)
    def get_pricelist(self, pricelist_id, include_rules=True, **kwargs):
        """GET /api/v1/pricelists/:id - Get single pricelist with rules"""
        return self._handle_route(lambda env: self._get_pricelist(env, pricelist_id, include_rules))
    
    def _get_pricelist(self, env, pricelist_id, include_rules):
        pricelist = env["product.pricelist"].browse(pricelist_id)
        if not pricelist.exists():
            raise MissingError(_("Pricelist #%s not found.") % pricelist_id)
        
        include = str(include_rules).lower() in ('true', '1', 'yes', 'on')
        return self._success(self._serialize_pricelist(pricelist, include))
    
    # ================================================================
    # PRICE CALCULATION ENDPOINTS
    # ================================================================
    
    @http.route("/api/v1/pricelists/<int:pricelist_id>/price", auth="public", methods=["POST"], type="http", csrf=False)
    def calculate_price(self, pricelist_id, **kwargs):
        """POST /api/v1/pricelists/:id/price - Calculate product price using this pricelist
        
        Request body:
        {
            "product_id": 123,
            "quantity": 5,
            "date": "2026-05-14",
            "uom_id": null
        }
        """
        return self._handle_route(lambda env: self._calculate_price(env, pricelist_id))
    
    def _calculate_price(self, env, pricelist_id):
        data = self._parse_json_data()
        
        pricelist = env["product.pricelist"].browse(pricelist_id)
        if not pricelist.exists():
            raise ValidationError(_("Pricelist #%s not found.") % pricelist_id)
        
        product_id = data.get("product_id")
        if not product_id:
            raise ValidationError(_("product_id is required."))
        
        product = env["product.product"].browse(int(product_id))
        if not product.exists():
            # Try product.template as fallback
            template = env["product.template"].browse(int(product_id))
            if template.exists() and template.product_variant_ids:
                product = template.product_variant_ids[0]
            else:
                raise ValidationError(_("Product #%s not found.") % product_id)
        
        quantity = float(data.get("quantity", 1))
        date_str = data.get("date")
        uom_id = data.get("uom_id")
        
        date = fields.Date.today()
        if date_str:
            try:
                date = fields.Date.from_string(date_str)
            except:
                pass
        
        uom = product.uom_id
        if uom_id:
            uom_obj = env["uom.uom"].browse(int(uom_id))
            if uom_obj.exists():
                uom = uom_obj
        
        # Calculate price using pricelist
        price = pricelist._get_product_price(
            product=product,
            quantity=quantity,
            uom=uom,
            date=date,
        )
        
        # Get applied rule for transparency
        rule_id = pricelist._get_product_rule(
            product=product,
            quantity=quantity,
            uom=uom,
            date=date,
        )
        rule = env["product.pricelist.item"].browse(rule_id) if rule_id else None
        
        # Calculate base price for comparison
        base_price = product.lst_price
        if uom != product.uom_id:
            base_price = product.uom_id._compute_price(base_price, uom)
        
        return self._success({
            "product_id": product.id,
            "product_name": product.display_name,
            "quantity": quantity,
            "base_price": base_price,
            "calculated_price": price,
            "currency_id": pricelist.currency_id.id,
            "currency_name": pricelist.currency_id.name,
            "currency_symbol": pricelist.currency_id.symbol,
            "applied_rule_id": rule.id if rule else None,
            "applied_rule_name": rule.name if rule else None,
            "pricelist_id": pricelist.id,
            "pricelist_name": pricelist.name,
        })
    
    @http.route("/api/v1/pricelists/calculate-batch", auth="public", methods=["POST"], type="http", csrf=False)
    def calculate_batch_prices(self, **kwargs):
        """POST /api/v1/pricelists/calculate-batch - Calculate prices for multiple products
        
        Request body:
        {
            "pricelist_id": 1,
            "items": [
                {"product_id": 123, "quantity": 5},
                {"product_id": 456, "quantity": 2}
            ],
            "date": "2026-05-14",
            "uom_id": null
        }
        """
        return self._handle_route(lambda env: self._calculate_batch_prices(env))
    
    def _calculate_batch_prices(self, env):
        data = self._parse_json_data()
        
        pricelist_id = data.get("pricelist_id")
        items = data.get("items", [])
        
        if not pricelist_id:
            raise ValidationError(_("pricelist_id is required."))
        if not items:
            raise ValidationError(_("items array is required."))
        
        pricelist = env["product.pricelist"].browse(pricelist_id)
        if not pricelist.exists():
            raise ValidationError(_("Pricelist #%s not found.") % pricelist_id)
        
        date_str = data.get("date")
        date = fields.Date.today()
        if date_str:
            try:
                date = fields.Date.from_string(date_str)
            except:
                pass
        
        results = []
        for item in items:
            product_id = item.get("product_id")
            if not product_id:
                continue
            
            product = env["product.product"].browse(int(product_id))
            if not product.exists():
                template = env["product.template"].browse(int(product_id))
                if template.exists() and template.product_variant_ids:
                    product = template.product_variant_ids[0]
                else:
                    results.append({
                        "product_id": product_id,
                        "error": "Product not found",
                    })
                    continue
            
            quantity = float(item.get("quantity", 1))
            uom_id = item.get("uom_id")
            
            uom = product.uom_id
            if uom_id:
                uom_obj = env["uom.uom"].browse(int(uom_id))
                if uom_obj.exists():
                    uom = uom_obj
            
            price = pricelist._get_product_price(
                product=product,
                quantity=quantity,
                uom=uom,
                date=date,
            )
            
            base_price = product.lst_price
            if uom != product.uom_id:
                base_price = product.uom_id._compute_price(base_price, uom)
            
            results.append({
                "product_id": product.id,
                "product_name": product.display_name,
                "quantity": quantity,
                "base_price": base_price,
                "calculated_price": price,
            })
        
        return self._success({
            "pricelist_id": pricelist.id,
            "pricelist_name": pricelist.name,
            "currency_id": pricelist.currency_id.id,
            "currency_name": pricelist.currency_id.name,
            "currency_symbol": pricelist.currency_id.symbol,
            "items": results,
        })
    
    # ================================================================
    # PRICELIST RULES ENDPOINTS
    # ================================================================
    
    @http.route("/api/v1/pricelists/<int:pricelist_id>/rules", auth="public", methods=["GET"], type="http", csrf=False)
    def list_rules(self, pricelist_id, **kwargs):
        """GET /api/v1/pricelists/:id/rules - List rules for a pricelist"""
        return self._handle_route(lambda env: self._list_rules(env, pricelist_id))
    
    def _list_rules(self, env, pricelist_id):
        pricelist = env["product.pricelist"].browse(pricelist_id)
        if not pricelist.exists():
            raise MissingError(_("Pricelist #%s not found.") % pricelist_id)
        
        return self._success({
            "pricelist_id": pricelist.id,
            "pricelist_name": pricelist.name,
            "rules": self._serialize_rules(pricelist.item_ids),
        })
    
    # ================================================================
    # CUSTOMER'S PRICELIST ENDPOINT
    # ================================================================
    
    @http.route("/api/v1/customers/<int:customer_id>/pricelist", auth="public", methods=["GET"], type="http", csrf=False)
    def get_customer_pricelist(self, customer_id, **kwargs):
        """GET /api/v1/customers/:id/pricelist - Get the pricelist assigned to a customer"""
        return self._handle_route(lambda env: self._get_customer_pricelist(env, customer_id))
    
    def _get_customer_pricelist(self, env, customer_id):
        customer = env["res.partner"].browse(customer_id)
        if not customer.exists():
            raise MissingError(_("Customer #%s not found.") % customer_id)
        
        pricelist = customer.property_product_pricelist
        
        if not pricelist:
            return self._success({
                "customer_id": customer.id,
                "customer_name": customer.name,
                "pricelist_id": None,
                "pricelist_name": None,
                "message": "No specific pricelist assigned to this customer. Using default pricing.",
            })
        
        return self._success({
            "customer_id": customer.id,
            "customer_name": customer.name,
            "pricelist_id": pricelist.id,
            "pricelist_name": pricelist.name,
            "pricelist_display_name": pricelist.display_name,
            "currency_id": pricelist.currency_id.id,
            "currency_name": pricelist.currency_id.name,
            "rules_count": len(pricelist.item_ids),
        })