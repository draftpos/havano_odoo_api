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

    def _resolve_component_product(self, env, line_data, line_index):
        """Resolve component variant; accept template id as fallback for POS."""
        product_id = line_data.get("product_id") or line_data.get(
            "component_product_id"
        )
        if not product_id:
            raise ValidationError(
                _(
                    "Line %(line)s: product_id is required (variant id from "
                    "product.product, not the bundle template id).",
                    line=line_index,
                )
            )

        try:
            pid = int(product_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                _("Line %(line)s: product_id must be a number, got %(value)r.", line=line_index, value=product_id)
            ) from exc

        product = env["product.product"].browse(pid)
        if not product.exists():
            tmpl = env["product.template"].browse(pid)
            if tmpl.exists():
                variant = tmpl.product_variant_id or (
                    tmpl.product_variant_ids[:1] if tmpl.product_variant_ids else False
                )
                if variant:
                    _logger.info(
                        "Bundle line %s: resolved template id %s to variant id %s",
                        line_index,
                        pid,
                        variant.id,
                    )
                    product = variant
            if not product.exists():
                raise ValidationError(
                    _(
                        "Line %(line)s: product #%(pid)s not found. Use the component "
                        "variant id (product.product), not the bundle parent id.",
                        line=line_index,
                        pid=pid,
                    )
                )

        product_type = product.type
        if "detailed_type" in product._fields:
            product_type = product.detailed_type
        if product_type == "service":
            raise ValidationError(
                _(
                    "Line %(line)s: product #%(pid)s (%(name)s) cannot be a "
                    "service product.",
                    line=line_index,
                    pid=product.id,
                    name=product.display_name,
                )
            )
        return product

    def _parse_bundle_line_vals(self, env, line_data, sequence=10, line_index=1):
        if not isinstance(line_data, dict):
            raise ValidationError(
                _(
                    "Line %(line)s: each entry in lines must be an object, got %(type)s.",
                    line=line_index,
                    type=type(line_data).__name__,
                )
            )

        component = self._resolve_component_product(env, line_data, line_index)
        try:
            qty = float(line_data.get("quantity", 1.0))
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                _("Line %(line)s: invalid quantity %(value)r.", line=line_index, value=line_data.get("quantity"))
            ) from exc
        if qty <= 0:
            raise ValidationError(
                _("Line %(line)s: quantity must be greater than zero.", line=line_index)
            )

        uom_id = line_data.get("uom_id")
        if uom_id:
            try:
                uom = env["uom.uom"].browse(int(uom_id))
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    _("Line %(line)s: invalid uom_id %(value)r.", line=line_index, value=uom_id)
                ) from exc
            if not uom.exists():
                raise ValidationError(
                    _("Line %(line)s: UOM #%(uom)s not found.", line=line_index, uom=uom_id)
                )
        else:
            uom = component.uom_id

        cost_price = line_data.get("cost_price")
        if cost_price is None:
            cost_price = component.standard_price

        sale_price = line_data.get("sale_price", line_data.get("unit_price"))
        if sale_price is None:
            sale_price = component.lst_price

        try:
            return {
                "sequence": line_data.get("sequence", sequence),
                "component_product_id": component.id,
                "quantity": qty,
                "uom_id": uom.id,
                "cost_price": float(cost_price),
                "unit_price": float(sale_price),
            }
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                _(
                    "Line %(line)s: invalid cost_price or sale_price.",
                    line=line_index,
                )
            ) from exc

    def _extract_bundle_lines(self, data):
        lines = data.get("lines")
        if lines is None:
            lines = data.get("bundle_lines")
        if lines is None:
            lines = data.get("bundle_line_ids")
        return lines

    def _apply_bundle_lines(self, product, lines_data):
        if lines_data is None:
            raise ValidationError(
                _(
                    "Missing bundle lines. Send a JSON body with a lines array, e.g. "
                    '{"lines": [{"product_id": 123, "quantity": 1, "sale_price": 10.0}]}'
                )
            )
        if not isinstance(lines_data, list):
            raise ValidationError(
                _(
                    "lines must be an array, got %(type)s.",
                    type=type(lines_data).__name__,
                )
            )
        if not lines_data:
            raise ValidationError(
                _("At least one bundle line is required in lines (array is empty).")
            )

        line_vals = []
        for idx, line_data in enumerate(lines_data):
            line_index = idx + 1
            try:
                line_vals.append(
                    (
                        0,
                        0,
                        self._parse_bundle_line_vals(
                            product.env,
                            line_data,
                            sequence=line_index * 10,
                            line_index=line_index,
                        ),
                    )
                )
            except ValidationError:
                raise
            except Exception as exc:
                _logger.exception(
                    "Bundle line parse failed product_id=%s line=%s",
                    product.id,
                    line_index,
                )
                raise ValidationError(
                    _("Line %(line)s: %(error)s", line=line_index, error=str(exc))
                ) from exc

        try:
            product.write({"bundle_line_ids": [(5, 0, 0)] + line_vals})
        except ValidationError:
            raise
        except Exception as exc:
            _logger.exception(
                "Bundle write failed product_id=%s lines=%s",
                product.id,
                len(line_vals),
            )
            raise ValidationError(
                _("Could not save bundle lines: %(error)s", error=str(exc))
            ) from exc

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
        type="http",
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
        lines = self._extract_bundle_lines(data)

        _logger.info(
            "Bundle API request product_id=%s keys=%s line_count=%s",
            product_id,
            sorted(data.keys()) if isinstance(data, dict) else type(data).__name__,
            len(lines) if isinstance(lines, list) else "n/a",
        )

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
            message=_("Product bundle lines saved successfully."),
        )
