from odoo import _, http
from odoo.exceptions import MissingError, ValidationError

from .common import HavanoApiControllerMixin

import logging

_logger = logging.getLogger(__name__)


class HavanoProductBundlesController(HavanoApiControllerMixin, http.Controller):
    """Bundle component lines for service bundle products."""

    def _bundle_enabled(self, env):
        return "is_product_bundle" in env["product.template"]._fields

    def _get_bundle_product(self, env, product_id):
        if not self._bundle_enabled(env):
            raise ValidationError(
                _("Product Bundle module is not installed on this server.")
            )
        product = env["product.template"].browse(product_id)
        if not product.exists():
            raise MissingError(_("Product #%s not found.") % product_id)
        if not product.is_product_bundle:
            raise ValidationError(
                _("Product #%s is not marked as a product bundle.") % product_id
            )
        return product

    def _serialize_bundle_line(self, line):
        return {
            "id": line.id,
            "sequence": line.sequence,
            "product_id": line.component_product_id.id,
            "product_name": line.component_product_id.display_name,
            "default_code": line.component_product_id.default_code or "",
            "quantity": line.quantity,
            "uom_id": line.uom_id.id if line.uom_id else None,
            "uom_name": line.uom_id.name if line.uom_id else "",
            "cost_price": line.cost_price,
            "cost_subtotal": line.cost_subtotal,
            "sale_price": line.unit_price,
            "sale_subtotal": line.subtotal,
            "modified_sale_price": line.modified_unit_price,
            "modified_sale_subtotal": line.modified_subtotal,
            "sale_price_ratio": line.sale_price_ratio,
        }

    def _serialize_bundle(self, product):
        lines = [self._serialize_bundle_line(line) for line in product.bundle_line_ids]
        return {
            "product_id": product.id,
            "product_name": product.name,
            "is_product_bundle": product.is_product_bundle,
            "list_price": product.list_price,
            "bundle_sale_total": product.bundle_total,
            "bundle_cost_total": product.bundle_cost_total,
            "bundle_price_overridden": product.bundle_price_overridden,
            "expand_bundle_in_so": product.expand_bundle_in_so,
            "lines": lines,
            "line_count": len(lines),
        }

    def _resolve_component_product(self, env, line_data):
        product_id = line_data.get("product_id") or line_data.get(
            "component_product_id"
        )
        if not product_id:
            raise ValidationError(
                _("Each bundle line requires product_id (component product).")
            )
        product = env["product.product"].browse(int(product_id))
        if not product.exists():
            raise ValidationError(_("Component product #%s not found.") % product_id)
        if product.type == "service":
            raise ValidationError(
                _("Bundle component #%s cannot be a service product.") % product_id
            )
        return product

    def _parse_bundle_line_vals(self, env, line_data, sequence=10):
        component = self._resolve_component_product(env, line_data)
        qty = float(line_data.get("quantity", 1.0))
        if qty <= 0:
            raise ValidationError(_("Bundle line quantity must be greater than zero."))

        uom_id = line_data.get("uom_id")
        if uom_id:
            uom = env["uom.uom"].browse(int(uom_id))
            if not uom.exists():
                raise ValidationError(_("UOM #%s not found.") % uom_id)
        else:
            uom = component.uom_id

        cost_price = line_data.get("cost_price")
        if cost_price is None:
            cost_price = component.standard_price

        sale_price = line_data.get("sale_price", line_data.get("unit_price"))
        if sale_price is None:
            sale_price = component.lst_price

        return {
            "sequence": line_data.get("sequence", sequence),
            "component_product_id": component.id,
            "quantity": qty,
            "uom_id": uom.id,
            "cost_price": float(cost_price),
            "unit_price": float(sale_price),
        }

    def _apply_bundle_lines(self, product, lines_data):
        if not lines_data:
            raise ValidationError(_("At least one bundle line is required."))
        line_vals = []
        for idx, line_data in enumerate(lines_data):
            line_vals.append(
                (
                    0,
                    0,
                    self._parse_bundle_line_vals(
                        product.env, line_data, sequence=(idx + 1) * 10
                    ),
                )
            )
        product.write({"bundle_line_ids": [(5, 0, 0)] + line_vals})

    @http.route(
        "/api/v1/products/<int:product_id>/bundle",
        auth="public",
        methods=["GET"],
        type="http",
        csrf=False,
    )
    def get_product_bundle(self, product_id, **kwargs):
        return self._handle_route(
            lambda env: self._get_product_bundle(env, product_id)
        )

    def _get_product_bundle(self, env, product_id):
        product = self._get_bundle_product(env, product_id)
        return self._success(self._serialize_bundle(product))

    @http.route(
        "/api/v1/products/<int:product_id>/bundle",
        auth="public",
        methods=["POST", "PUT", "PATCH"],
        type="json",
        csrf=False,
    )
    def set_product_bundle(self, product_id, **kwargs):
        return self._handle_route(
            lambda env: self._set_product_bundle(env, product_id)
        )

    def _set_product_bundle(self, env, product_id):
        product = env["product.template"].browse(product_id)
        if not product.exists():
            raise MissingError(_("Product #%s not found.") % product_id)

        if not self._bundle_enabled(env):
            raise ValidationError(
                _("Product Bundle module is not installed on this server.")
            )

        data = self._parse_json_data()
        write_vals = {}

        if not product.is_product_bundle:
            write_vals["is_product_bundle"] = True
            write_vals["type"] = "service"
            if "detailed_type" in product._fields:
                write_vals["detailed_type"] = "service"
            write_vals.setdefault("sale_ok", True)

        if "list_price" in data:
            write_vals["list_price"] = float(data["list_price"])
        if "expand_bundle_in_so" in data:
            write_vals["expand_bundle_in_so"] = bool(data["expand_bundle_in_so"])

        if write_vals:
            product.write(write_vals)

        lines = data.get("lines") or data.get("bundle_lines") or data.get(
            "bundle_line_ids"
        )
        if lines is None:
            raise ValidationError(
                _("Bundle lines are required (lines / bundle_lines).")
            )

        self._apply_bundle_lines(product, lines)

        if "list_price" not in data:
            product.write({"list_price": product.bundle_total})

        _logger.info(
            "Bundle updated via API: product_id=%s lines=%s total=%s",
            product.id,
            len(product.bundle_line_ids),
            product.list_price,
        )
        return self._success(
            self._serialize_bundle(product),
            message=_("Product bundle components saved."),
        )
