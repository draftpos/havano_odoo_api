from odoo import _, http
from odoo.exceptions import AccessDenied, MissingError, ValidationError

from .common import HavanoApiControllerMixin

import logging

_logger = logging.getLogger(__name__)


class HavanoUsersController(HavanoApiControllerMixin, http.Controller):
    """CRUD API for res.users including Havano addon roles."""

    def _ensure_user_manager(self, env):
        if not env.user.has_group("base.group_system"):
            raise AccessDenied(
                _("Only administrators can manage users via the API.")
            )

    def _partner_phone(self, user):
        partner = user.partner_id
        if not partner:
            return "", ""
        mobile = partner.mobile if "mobile" in partner._fields else ""
        return partner.phone or "", mobile or ""

    def _company_payload(self, user):
        company = user.company_id
        return {
            "id": company.id if company else None,
            "name": company.name if company else "",
        }

    def _serialize_user(self, user):
        phone, mobile = self._partner_phone(user)
        roles = user._havano_roles_payload()
        company = self._company_payload(user)
        return {
            "id": user.id,
            "name": user.name or "",
            "login": user.login or "",
            "email": user.email or "",
            "phone": phone,
            "mobile": mobile,
            "active": user.active,
            "share": user.share,
            "lang": user.lang or "",
            "tz": user.tz or "",
            "company": company,
            "company_id": company["id"],
            "company_name": company["name"],
            "company_ids": [
                {"id": c.id, "name": c.name} for c in user.company_ids
            ],
            "roles": roles,
            "role": roles["role"],
            "is_user": roles["is_user"],
            "is_administrator": roles["is_administrator"],
            "is_pharmacist": roles["is_pharmacist"],
            "is_cashier": roles["is_cashier"],
            "create_date": str(user.create_date) if user.create_date else None,
            "write_date": str(user.write_date) if user.write_date else None,
        }

    def _roles_reference(self):
        return {
            "base_roles": [
                {
                    "id": "group_user",
                    "name": "User",
                    "field": "role",
                    "value": "group_user",
                },
                {
                    "id": "group_system",
                    "name": "Administrator",
                    "field": "role",
                    "value": "group_system",
                },
            ],
            "addon_roles": [
                {
                    "id": "pharmacist",
                    "name": "Pharmacist",
                    "field": "is_pharmacist",
                    "requires_user_or_administrator": True,
                },
                {
                    "id": "cashier",
                    "name": "Cashier",
                    "field": "is_cashier",
                    "requires_user_or_administrator": True,
                },
            ],
            "valid_combinations": [
                "user",
                "user + pharmacist",
                "user + cashier",
                "user + pharmacist + cashier",
                "administrator",
                "administrator + pharmacist",
                "administrator + cashier",
                "administrator + pharmacist + cashier",
            ],
        }

    def _validate_addon_roles(self, vals):
        """Pharmacist/Cashier cannot exist without User or Administrator."""
        if not vals.get("is_pharmacist") and not vals.get("is_cashier"):
            return
        role = vals.get("role")
        if role in (False, None, "") and "role" not in vals:
            return
        if role in (False, None, ""):
            raise ValidationError(
                _(
                    "Pharmacist and Cashier require role 'group_user' or "
                    "'group_system'."
                )
            )

    def _parse_user_vals(self, data, for_create=False):
        vals = {}
        if "name" in data:
            vals["name"] = data.get("name")
        if "login" in data:
            vals["login"] = data.get("login")
        if "email" in data:
            vals["email"] = data.get("email")
        if "phone" in data:
            vals["phone"] = data.get("phone")
        if "mobile" in data:
            vals["mobile"] = data.get("mobile")
        if "active" in data:
            vals["active"] = bool(data.get("active"))
        if "lang" in data:
            vals["lang"] = data.get("lang")
        if "tz" in data:
            vals["tz"] = data.get("tz")
        if data.get("password"):
            vals["password"] = data.get("password")
        if data.get("company_id"):
            vals["company_id"] = int(data["company_id"])
        if data.get("company_ids"):
            vals["company_ids"] = [(6, 0, [int(x) for x in data["company_ids"]])]

        roles = data.get("roles") or {}
        if "role" in data:
            role = data.get("role")
        elif "role" in roles:
            role = roles.get("role")
        else:
            role = None
        if role is not None:
            if role in ("group_user", "group_system", False, "", None):
                vals["role"] = role or False
            else:
                raise ValidationError(
                    _("Invalid role. Use 'group_user', 'group_system', or false.")
                )
        elif for_create:
            vals.setdefault("role", "group_user")

        if "is_pharmacist" in data or roles.get("is_pharmacist") is not None:
            vals["is_pharmacist"] = bool(
                data.get("is_pharmacist", roles.get("is_pharmacist"))
            )
        if "is_cashier" in data or roles.get("is_cashier") is not None:
            vals["is_cashier"] = bool(data.get("is_cashier", roles.get("is_cashier")))

        if vals.get("is_pharmacist") or vals.get("is_cashier"):
            if vals.get("role") in (False, None, ""):
                vals["role"] = "group_user"

        self._validate_addon_roles(vals)
        return vals

    @http.route(
        "/api/v1/users/me",
        auth="public",
        methods=["GET"],
        type="http",
        csrf=False,
    )
    def get_current_user(self, **kwargs):
        """Return the authenticated user's profile."""
        return self._handle_route(lambda env: self._get_current_user(env))

    def _get_current_user(self, env):
        user = env.user
        if not user or not user.id:
            raise AccessDenied(_("Not authenticated."))
        return self._success(self._serialize_user(user))

    @http.route(
        "/api/v1/users/roles",
        auth="public",
        methods=["GET"],
        type="http",
        csrf=False,
    )
    def roles_reference(self, **kwargs):
        return self._handle_route(lambda env: self._roles_reference_handler(env))

    def _roles_reference_handler(self, env):
        self._ensure_user_manager(env)
        return self._success(self._roles_reference())

    @http.route("/api/v1/users", auth="public", methods=["GET"], type="http", csrf=False)
    def list_users(self, limit=100, offset=0, active_only="true", **kwargs):
        return self._handle_route(
            lambda env: self._list_users(env, limit, offset, active_only)
        )

    def _list_users(self, env, limit, offset, active_only):
        self._ensure_user_manager(env)
        try:
            limit = min(int(limit), 500)
            offset = int(offset) if offset else 0
        except (ValueError, TypeError):
            limit, offset = 100, 0

        domain = [("share", "=", False)]
        if str(active_only).lower() in ("1", "true", "yes"):
            domain.append(("active", "=", True))

        users = env["res.users"].search(
            domain, limit=limit, offset=offset, order="name"
        )
        total = env["res.users"].search_count(domain)
        return self._success(
            {
                "items": [self._serialize_user(u) for u in users],
                "total": total,
                "limit": limit,
                "offset": offset,
                "roles_reference": self._roles_reference(),
            }
        )

    @http.route(
        "/api/v1/users/<int:user_id>",
        auth="public",
        methods=["GET"],
        type="http",
        csrf=False,
    )
    def get_user(self, user_id, **kwargs):
        return self._handle_route(lambda env: self._get_user(env, user_id))

    def _get_user(self, env, user_id):
        if env.user.id == user_id:
            return self._success(self._serialize_user(env.user))
        self._ensure_user_manager(env)
        user = env["res.users"].browse(user_id)
        if not user.exists() or user.share:
            raise MissingError(_("User #%s not found.") % user_id)
        return self._success(self._serialize_user(user))

    @http.route(
        "/api/v1/users",
        auth="public",
        methods=["POST"],
        type="json",
        csrf=False,
    )
    def create_user(self, **kwargs):
        return self._handle_route(lambda env: self._create_user(env))

    def _create_user(self, env):
        self._ensure_user_manager(env)
        data = self._parse_json_data()
        if not data.get("name"):
            raise ValidationError(_("User name is required."))
        if not data.get("login"):
            raise ValidationError(_("User login is required."))
        if not data.get("password"):
            raise ValidationError(_("Password is required when creating a user."))

        vals = self._parse_user_vals(data, for_create=True)
        vals.setdefault("active", True)
        user = env["res.users"].create(vals)
        _logger.info("User created via API: id=%s login=%s", user.id, user.login)
        return self._success(
            self._serialize_user(user),
            message=_("User created."),
            status=201,
        )

    @http.route(
        "/api/v1/users/<int:user_id>",
        auth="public",
        methods=["PUT", "PATCH", "POST"],
        type="json",
        csrf=False,
    )
    def update_user(self, user_id, **kwargs):
        return self._handle_route(lambda env: self._update_user(env, user_id))

    def _update_user(self, env, user_id):
        data = self._parse_json_data()
        user = env["res.users"].browse(user_id)
        if not user.exists() or user.share:
            raise MissingError(_("User #%s not found.") % user_id)
        if env.user.id != user_id:
            self._ensure_user_manager(env)
        vals = self._parse_user_vals(data)
        if not vals:
            raise ValidationError(_("No data provided for update."))
        user.write(vals)
        return self._success(
            self._serialize_user(user),
            message=_("User updated."),
        )

    @http.route(
        "/api/v1/users/<int:user_id>",
        auth="public",
        methods=["DELETE"],
        type="json",
        csrf=False,
    )
    def delete_user(self, user_id, **kwargs):
        return self._handle_route(lambda env: self._delete_user(env, user_id))

    def _delete_user(self, env, user_id):
        self._ensure_user_manager(env)
        user = env["res.users"].browse(user_id)
        if not user.exists() or user.share:
            raise MissingError(_("User #%s not found.") % user_id)
        if user.id == env.user.id:
            raise ValidationError(_("You cannot archive your own user account."))
        user.write({"active": False})
        return self._success(
            {"id": user.id, "active": False},
            message=_("User archived."),
        )
