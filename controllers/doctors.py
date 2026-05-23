from odoo import _, http
from odoo.exceptions import MissingError, ValidationError

from .common import HavanoApiControllerMixin

import logging

_logger = logging.getLogger(__name__)


class HavanoDoctorsController(HavanoApiControllerMixin, http.Controller):

    def _pharmacy_enabled(self, env):
        return bool(env.company.hao_activate_pharmacy)

    def _serialize_doctor(self, partner):
        cert_b64 = ""
        if partner.doctor_certificate:
            cert_b64 = partner.doctor_certificate.decode() if isinstance(partner.doctor_certificate, bytes) else partner.doctor_certificate
        return {
            "id": partner.id,
            "name": partner.name or "",
            "display_name": partner.display_name or "",
            "is_doctor": partner.is_doctor,
            "contact_type": partner.contact_type or "",
            "doctor_reg_no": partner.doctor_reg_no or "",
            "doctor_certificate_filename": partner.doctor_certificate_filename or "",
            "doctor_certificate": cert_b64,
            "phone": partner.phone or "",
            "email": partner.email or "",
            "street": partner.street or "",
            "street2": partner.street2 or "",
            "city": partner.city or "",
            "zip": partner.zip or "",
            "state_id": partner.state_id.id if partner.state_id else None,
            "state_name": partner.state_id.name or "",
            "country_id": partner.country_id.id if partner.country_id else None,
            "country_name": partner.country_id.name or "",
            "active": partner.active,
            "create_date": str(partner.create_date) if partner.create_date else None,
            "write_date": str(partner.write_date) if partner.write_date else None,
        }

    def _parse_doctor_vals(self, data, for_create=False):
        vals = {}
        if "name" in data:
            vals["name"] = data.get("name")
        if "phone" in data:
            vals["phone"] = data.get("phone")
        if "email" in data:
            vals["email"] = data.get("email")
        if "street" in data:
            vals["street"] = data.get("street")
        if "street2" in data:
            vals["street2"] = data.get("street2")
        if "city" in data:
            vals["city"] = data.get("city")
        if "zip" in data:
            vals["zip"] = data.get("zip")
        if data.get("state_id"):
            vals["state_id"] = int(data["state_id"])
        if data.get("country_id"):
            vals["country_id"] = int(data["country_id"])
        if "doctor_reg_no" in data or "reg_no" in data:
            vals["doctor_reg_no"] = data.get("doctor_reg_no") or data.get("reg_no")
        if "doctor_certificate" in data or "certificate" in data:
            raw = data.get("doctor_certificate") or data.get("certificate")
            if raw:
                vals["doctor_certificate"] = raw
        if "doctor_certificate_filename" in data or "certificate_filename" in data:
            vals["doctor_certificate_filename"] = (
                data.get("doctor_certificate_filename") or data.get("certificate_filename")
            )
        if for_create or data.get("is_doctor") is not False:
            vals["is_doctor"] = True
        return vals

    @http.route("/api/v1/doctors", auth="public", methods=["GET"], type="http", csrf=False)
    def list_doctors(self, limit=100, offset=0, **kwargs):
        return self._handle_route(lambda env: self._list_doctors(env, limit, offset))

    def _list_doctors(self, env, limit, offset):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        try:
            limit = min(int(limit), 500)
            offset = int(offset) if offset else 0
        except (ValueError, TypeError):
            limit, offset = 100, 0
        domain = [("is_doctor", "=", True), ("active", "=", True)]
        partners = env["res.partner"].search(domain, limit=limit, offset=offset, order="name")
        total = env["res.partner"].search_count(domain)
        return self._success({
            "items": [self._serialize_doctor(p) for p in partners],
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @http.route("/api/v1/doctors/<int:doctor_id>", auth="public", methods=["GET"], type="http", csrf=False)
    def get_doctor(self, doctor_id, **kwargs):
        return self._handle_route(lambda env: self._get_doctor(env, doctor_id))

    def _get_doctor(self, env, doctor_id):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        partner = env["res.partner"].browse(doctor_id)
        if not partner.exists() or not partner.is_doctor:
            raise MissingError(_("Doctor #%s not found.") % doctor_id)
        return self._success(self._serialize_doctor(partner))

    @http.route("/api/v1/doctors", auth="public", methods=["POST"], type="http", csrf=False)
    def create_doctor(self, **kwargs):
        return self._handle_route(lambda env: self._create_doctor(env))

    def _create_doctor(self, env):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        data = self._parse_json_data()
        if not data.get("name"):
            raise ValidationError(_("Doctor name is required."))
        vals = self._parse_doctor_vals(data, for_create=True)
        if not vals.get("doctor_reg_no"):
            raise ValidationError(_("Doctor REG Number is required."))
        vals.setdefault("is_customer", False)
        vals.setdefault("is_supplier", False)
        partner = env["res.partner"].create(vals)
        _logger.info("Doctor created via API: id=%s name=%s", partner.id, partner.name)
        return self._success(self._serialize_doctor(partner), message=_("Doctor created."), status=201)

    @http.route("/api/v1/doctors/<int:doctor_id>", auth="public", methods=["PUT", "PATCH", "POST"], type="http", csrf=False)
    def update_doctor(self, doctor_id, **kwargs):
        return self._handle_route(lambda env: self._update_doctor(env, doctor_id))

    def _update_doctor(self, env, doctor_id):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        data = self._parse_json_data()
        partner = env["res.partner"].browse(doctor_id)
        if not partner.exists():
            raise MissingError(_("Doctor #%s not found.") % doctor_id)
        vals = self._parse_doctor_vals(data)
        if not vals:
            raise ValidationError(_("No data provided for update."))
        vals["is_doctor"] = True
        partner.write(vals)
        return self._success(self._serialize_doctor(partner), message=_("Doctor updated."))

    @http.route("/api/v1/doctors/<int:doctor_id>", auth="public", methods=["DELETE"], type="http", csrf=False)
    def delete_doctor(self, doctor_id, **kwargs):
        return self._handle_route(lambda env: self._delete_doctor(env, doctor_id))

    def _delete_doctor(self, env, doctor_id):
        if not self._pharmacy_enabled(env):
            raise ValidationError(_("Pharmacy is not activated on this company."))
        partner = env["res.partner"].browse(doctor_id)
        if not partner.exists():
            raise MissingError(_("Doctor #%s not found.") % doctor_id)
        partner.write({"active": False, "is_doctor": False})
        return self._success({"id": partner.id, "active": False}, message=_("Doctor archived."))
