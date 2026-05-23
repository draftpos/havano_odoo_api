from odoo import _, http
from odoo.exceptions import MissingError, ValidationError

from .common import HavanoApiControllerMixin

import logging

_logger = logging.getLogger(__name__)


class HavanoDosagesController(HavanoApiControllerMixin, http.Controller):

    def _pharmacy_enabled(self, env):
        return bool(env.company.hao_activate_pharmacy)

    def _serialize_dosage(self, dosage):
        return {
            "id": dosage.id,
            "code": dosage.code or "",
            "description": dosage.description or "",
            "active": dosage.active,
            "display_name": dosage.display_name,
            "create_date": str(dosage.create_date) if dosage.create_date else None,
            "write_date": str(dosage.write_date) if dosage.write_date else None,
        }

    def _parse_dosage_vals(self, data):
        vals = {}
        if "code" in data:
            vals["code"] = (data.get("code") or "").strip()
        if "description" in data:
            vals["description"] = data.get("description") or ""
        if "active" in data:
            vals["active"] = bool(data.get("active"))
        return vals

    @http.route("/api/v1/dosages", auth="public", methods=["GET"], type="http", csrf=False)
    def list_dosages(self, limit=200, offset=0, **kwargs):
        return self._handle_route(lambda env: self._list_dosages(env, limit, offset))

    def _list_dosages(self, env, limit, offset):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        try:
            limit = min(int(limit), 500)
            offset = int(offset) if offset else 0
        except (ValueError, TypeError):
            limit, offset = 200, 0
        domain = [("active", "=", True)]
        records = env["pharmacy.dosage"].search(domain, limit=limit, offset=offset, order="code")
        total = env["pharmacy.dosage"].search_count(domain)
        return self._success({
            "items": [self._serialize_dosage(r) for r in records],
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @http.route("/api/v1/dosages/<int:dosage_id>", auth="public", methods=["GET"], type="http", csrf=False)
    def get_dosage(self, dosage_id, **kwargs):
        return self._handle_route(lambda env: self._get_dosage(env, dosage_id))

    def _get_dosage(self, env, dosage_id):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        dosage = env["pharmacy.dosage"].browse(dosage_id)
        if not dosage.exists():
            raise MissingError(_("Dosage #%s not found.") % dosage_id)
        return self._success(self._serialize_dosage(dosage))

    @http.route("/api/v1/dosages", auth="public", methods=["POST"], type="http", csrf=False)
    def create_dosage(self, **kwargs):
        return self._handle_route(lambda env: self._create_dosage(env))

    def _create_dosage(self, env):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        data = self._parse_json_data()
        vals = self._parse_dosage_vals(data)
        if not vals.get("code"):
            raise ValidationError(_("Dosage code is required."))
        if not vals.get("description"):
            raise ValidationError(_("Dosage description is required."))
        existing = env["pharmacy.dosage"].search([("code", "=", vals["code"])], limit=1)
        if existing:
            existing.write(vals)
            return self._success(self._serialize_dosage(existing), message=_("Dosage updated."), status=200)
        dosage = env["pharmacy.dosage"].create(vals)
        return self._success(self._serialize_dosage(dosage), message=_("Dosage created."), status=201)

    @http.route("/api/v1/dosages/<int:dosage_id>", auth="public", methods=["PUT", "PATCH", "POST"], type="http", csrf=False)
    def update_dosage(self, dosage_id, **kwargs):
        return self._handle_route(lambda env: self._update_dosage(env, dosage_id))

    def _update_dosage(self, env, dosage_id):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        data = self._parse_json_data()
        dosage = env["pharmacy.dosage"].browse(dosage_id)
        if not dosage.exists():
            raise MissingError(_("Dosage #%s not found.") % dosage_id)
        vals = self._parse_dosage_vals(data)
        if not vals:
            raise ValidationError(_("No data provided for update."))
        dosage.write(vals)
        return self._success(self._serialize_dosage(dosage), message=_("Dosage updated."))

    @http.route("/api/v1/dosages/<int:dosage_id>", auth="public", methods=["DELETE"], type="http", csrf=False)
    def delete_dosage(self, dosage_id, **kwargs):
        return self._handle_route(lambda env: self._delete_dosage(env, dosage_id))

    def _delete_dosage(self, env, dosage_id):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        dosage = env["pharmacy.dosage"].browse(dosage_id)
        if not dosage.exists():
            raise MissingError(_("Dosage #%s not found.") % dosage_id)
        dosage.write({"active": False})
        return self._success({"id": dosage.id, "active": False}, message=_("Dosage archived."))
