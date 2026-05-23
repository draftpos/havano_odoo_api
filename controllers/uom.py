from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import MissingError, ValidationError
from .common import HavanoApiControllerMixin

import logging
_logger = logging.getLogger(__name__)


class HavanoUomController(HavanoApiControllerMixin, http.Controller):
    
    # ================================================================
    # UNIT OF MEASURE (uom.uom) ENDPOINTS
    # ================================================================
    
    def _serialize_uom(self, uom):
        """Convert uom.uom to API response dict."""
        return {
            "id": uom.id,
            "name": uom.name,
            "display_name": uom.display_name,
            "factor": uom.factor,
            "rounding": uom.rounding,
            "active": uom.active,
            "relative_factor": uom.relative_factor,
            "relative_uom_id": uom.relative_uom_id.id if uom.relative_uom_id else None,
            "relative_uom_name": uom.relative_uom_id.name if uom.relative_uom_id else None,
            "sequence": uom.sequence,
        }
    
    @http.route("/api/v1/uom", auth="public", methods=["GET"], type="http", csrf=False)
    def list_uoms(self, active_only=True, limit=100, offset=0, **kwargs):
        """GET /api/v1/uom - List all Units of Measure
        
        Query params:
        - active_only: bool (default True)
        - limit: int (default 100, max 500)
        - offset: int (default 0)
        """
        return self._handle_route(lambda env: self._list_uoms(env, active_only, limit, offset))
    
    def _list_uoms(self, env, active_only, limit, offset):
        try:
            limit = min(int(limit), 500)
            offset = int(offset) if offset else 0
            active_only = str(active_only).lower() in ('true', '1', 'yes', 'on')
        except (ValueError, TypeError):
            limit, offset = 100, 0
        
        domain = []
        if active_only:
            domain.append(("active", "=", True))
        
        # Use default order from model: 'sequence, relative_uom_id, id'
        uoms = env["uom.uom"].search(domain, limit=limit, offset=offset)
        
        items = []
        for uom in uoms:
            items.append({
                "id": uom.id,
                "name": uom.name,
                "display_name": uom.display_name,
                "factor": uom.factor,
                "rounding": uom.rounding,
                "active": uom.active,
                "relative_factor": uom.relative_factor,
                "relative_uom_id": uom.relative_uom_id.id if uom.relative_uom_id else None,
                "relative_uom_name": uom.relative_uom_id.name if uom.relative_uom_id else None,
                "sequence": uom.sequence,
            })
        
        total = env["uom.uom"].search_count(domain)
        
        return self._success({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        })
    
    @http.route("/api/v1/uom/<int:uom_id>", auth="public", methods=["GET"], type="http", csrf=False)
    def get_uom(self, uom_id, **kwargs):
        """GET /api/v1/uom/:id - Get single Unit of Measure"""
        return self._handle_route(lambda env: self._get_uom(env, uom_id))
    
    def _get_uom(self, env, uom_id):
        uom = env["uom.uom"].browse(uom_id)
        if not uom.exists():
            raise MissingError(_("Unit of Measure #%s not found.") % uom_id)
        return self._success(self._serialize_uom(uom))
    
    @http.route("/api/v1/uom/convert", auth="public", methods=["POST"], type="http", csrf=False)
    def convert_quantity(self, **kwargs):
        """POST /api/v1/uom/convert - Convert quantity between UoMs
        
        Request body:
        {
            "from_uom_id": 1,
            "to_uom_id": 2,
            "quantity": 5.0
        }
        """
        return self._handle_route(lambda env: self._convert_quantity(env))
    
    def _convert_quantity(self, env):
        data = self._parse_json_data()
        
        from_uom_id = data.get("from_uom_id")
        to_uom_id = data.get("to_uom_id")
        quantity = float(data.get("quantity", 1))
        
        if not from_uom_id:
            raise ValidationError(_("from_uom_id is required."))
        if not to_uom_id:
            raise ValidationError(_("to_uom_id is required."))
        
        from_uom = env["uom.uom"].browse(int(from_uom_id))
        if not from_uom.exists():
            raise ValidationError(_("Source UoM #%s not found.") % from_uom_id)
        
        to_uom = env["uom.uom"].browse(int(to_uom_id))
        if not to_uom.exists():
            raise ValidationError(_("Target UoM #%s not found.") % to_uom_id)
        
        # Conversion works directly between any UoMs using factor
        try:
            converted = from_uom._compute_quantity(quantity, to_uom)
            compatible = True
        except Exception:
            converted = quantity
            compatible = False
        
        return self._success({
            "from_uom_id": from_uom.id,
            "from_uom_name": from_uom.name,
            "to_uom_id": to_uom.id,
            "to_uom_name": to_uom.name,
            "quantity": quantity,
            "converted_quantity": converted,
            "factor": from_uom.factor / to_uom.factor if to_uom.factor else 1,
            "compatible": compatible,
        })
    
    # ================================================================
    # PRODUCT PACKAGING (product.uom) ENDPOINTS
    # ================================================================
    
    def _serialize_packaging(self, packaging):
        """Convert product.uom (packaging/barcode) to API response dict."""
        return {
            "id": packaging.id,
            "barcode": packaging.barcode,
            "display_name": packaging.display_name,
            "uom_id": packaging.uom_id.id,
            "uom_name": packaging.uom_id.name,
            "uom_factor": packaging.uom_id.factor,
            "uom_rounding": packaging.uom_id.rounding,
            "product_id": packaging.product_id.id,
            "product_name": packaging.product_id.display_name,
            "product_default_code": packaging.product_id.default_code,
            "company_id": packaging.company_id.id if packaging.company_id else None,
            "company_name": packaging.company_id.name if packaging.company_id else None,
        }
    
    @http.route("/api/v1/product-packagings", auth="public", methods=["GET"], type="http", csrf=False)
    def list_packagings(self, product_id=None, limit=100, offset=0, **kwargs):
        """GET /api/v1/product-packagings - List product packagings (barcodes)
        
        Query params:
        - product_id: int (optional) - filter by product
        - limit: int (default 100, max 500)
        - offset: int (default 0)
        """
        return self._handle_route(lambda env: self._list_packagings(env, product_id, limit, offset))
    
    def _list_packagings(self, env, product_id, limit, offset):
        try:
            limit = min(int(limit), 500)
            offset = int(offset) if offset else 0
        except (ValueError, TypeError):
            limit, offset = 100, 0
        
        domain = []
        if product_id:
            domain.append(("product_id", "=", int(product_id)))
        
        packagings = env["product.uom"].search(domain, limit=limit, offset=offset)
        
        items = []
        for p in packagings:
            items.append(self._serialize_packaging(p))
        
        total = env["product.uom"].search_count(domain)
        
        return self._success({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        })
    
    @http.route("/api/v1/product-packagings/<int:packaging_id>", auth="public", methods=["GET"], type="http", csrf=False)
    def get_packaging(self, packaging_id, **kwargs):
        """GET /api/v1/product-packagings/:id - Get single packaging"""
        return self._handle_route(lambda env: self._get_packaging(env, packaging_id))
    
    def _get_packaging(self, env, packaging_id):
        packaging = env["product.uom"].browse(packaging_id)
        if not packaging.exists():
            raise MissingError(_("Packaging #%s not found.") % packaging_id)
        return self._success(self._serialize_packaging(packaging))
    
    @http.route("/api/v1/product-packagings/by-barcode/<string:barcode>", auth="public", methods=["GET"], type="http", csrf=False)
    def get_packaging_by_barcode(self, barcode, **kwargs):
        """GET /api/v1/product-packagings/by-barcode/{barcode} - Find packaging by barcode"""
        return self._handle_route(lambda env: self._get_packaging_by_barcode(env, barcode))
    
    def _get_packaging_by_barcode(self, env, barcode):
        packaging = env["product.uom"].search([("barcode", "=", barcode)], limit=1)
        if not packaging:
            raise MissingError(_("Packaging with barcode '%s' not found.") % barcode)
        return self._success(self._serialize_packaging(packaging))
    
    @http.route("/api/v1/products/<int:product_id>/packagings", auth="public", methods=["GET"], type="http", csrf=False)
    def get_product_packagings(self, product_id, **kwargs):
        """GET /api/v1/products/:id/packagings - Get all packagings for a product"""
        return self._handle_route(lambda env: self._get_product_packagings(env, product_id))
    
    def _get_product_packagings(self, env, product_id):
        product = env["product.product"].browse(product_id)
        if not product.exists():
            # Try product.template
            template = env["product.template"].browse(product_id)
            if template.exists() and template.product_variant_ids:
                product = template.product_variant_ids[0]
            else:
                raise MissingError(_("Product #%s not found.") % product_id)
        
        packagings = env["product.uom"].search([("product_id", "=", product.id)])
        
        return self._success({
            "product_id": product.id,
            "product_name": product.display_name,
            "default_code": product.default_code,
            "default_uom_id": product.uom_id.id,
            "default_uom_name": product.uom_id.name,
            "packagings": [self._serialize_packaging(p) for p in packagings],
            "total": len(packagings),
        })
    
    # ================================================================
    # CREATE/UPDATE PACKAGING ENDPOINTS
    # ================================================================
    
    @http.route("/api/v1/product-packagings", auth="public", methods=["POST"], type="http", csrf=False)
    def create_packaging(self, **kwargs):
        """POST /api/v1/product-packagings - Create product packaging
        
        Request body:
        {
            "product_id": 123,
            "uom_id": 1,
            "barcode": "1234567890"
        }
        """
        return self._handle_route(lambda env: self._create_packaging(env))
    
    def _create_packaging(self, env):
        data = self._parse_json_data()
        
        product_id = data.get("product_id")
        uom_id = data.get("uom_id")
        barcode = data.get("barcode")
        
        if not product_id:
            raise ValidationError(_("product_id is required."))
        if not uom_id:
            raise ValidationError(_("uom_id is required."))
        if not barcode:
            raise ValidationError(_("barcode is required."))
        
        product = env["product.product"].browse(int(product_id))
        if not product.exists():
            raise ValidationError(_("Product #%s not found.") % product_id)
        
        uom = env["uom.uom"].browse(int(uom_id))
        if not uom.exists():
            raise ValidationError(_("UoM #%s not found.") % uom_id)
        
        # Check if barcode already exists
        existing = env["product.uom"].search([("barcode", "=", barcode)], limit=1)
        if existing:
            raise ValidationError(_("Packaging with barcode '%s' already exists.") % barcode)
        
        packaging = env["product.uom"].create({
            "product_id": product.id,
            "uom_id": uom.id,
            "barcode": barcode,
            "company_id": data.get("company_id", env.company.id),
        })
        
        _logger.info("Packaging created: id=%s, barcode=%s, product=%s", 
                    packaging.id, packaging.barcode, product.display_name)
        
        return self._success(self._serialize_packaging(packaging), 
                           message=_("Packaging created."), status=201)
    
    @http.route("/api/v1/product-packagings/<int:packaging_id>", auth="public", methods=["PUT", "POST"], type="http", csrf=False)
    def update_packaging(self, packaging_id, **kwargs):
        """PUT /api/v1/product-packagings/:id - Update packaging"""
        return self._handle_route(lambda env: self._update_packaging(env, packaging_id))
    
    def _update_packaging(self, env, packaging_id):
        data = self._parse_json_data()
        
        packaging = env["product.uom"].browse(packaging_id)
        if not packaging.exists():
            raise MissingError(_("Packaging #%s not found.") % packaging_id)
        
        if "barcode" in data:
            # Check uniqueness
            existing = env["product.uom"].search([
                ("barcode", "=", data["barcode"]),
                ("id", "!=", packaging.id),
            ], limit=1)
            if existing:
                raise ValidationError(_("Packaging with barcode '%s' already exists.") % data["barcode"])
        
        if "uom_id" in data:
            uom = env["uom.uom"].browse(int(data["uom_id"]))
            if not uom.exists():
                raise ValidationError(_("UoM #%s not found.") % data["uom_id"])
        
        packaging.write(data)
        
        _logger.info("Packaging updated: id=%s, barcode=%s", packaging.id, packaging.barcode)
        
        return self._success(self._serialize_packaging(packaging), 
                           message=_("Packaging updated."))
    
    @http.route("/api/v1/product-packagings/<int:packaging_id>", auth="public", methods=["DELETE"], type="http", csrf=False)
    def delete_packaging(self, packaging_id, **kwargs):
        """DELETE /api/v1/product-packagings/:id - Delete packaging"""
        return self._handle_route(lambda env: self._delete_packaging(env, packaging_id))
    
    def _delete_packaging(self, env, packaging_id):
        packaging = env["product.uom"].browse(packaging_id)
        if not packaging.exists():
            raise MissingError(_("Packaging #%s not found.") % packaging_id)
        
        barcode = packaging.barcode
        packaging.unlink()
        
        _logger.info("Packaging deleted: id=%s, barcode=%s", packaging_id, barcode)
        
        return self._success({"id": packaging_id, "barcode": barcode}, 
                           message=_("Packaging deleted."))