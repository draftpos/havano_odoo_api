# controllers/common.py
import json
import logging
from functools import wraps
from datetime import datetime

from odoo import _
from odoo.exceptions import AccessDenied, MissingError, ValidationError
from odoo.http import Response, request

_logger = logging.getLogger(__name__)


class HavanoApiControllerMixin:
    """
    Mixin for Havano API controllers with HYBRID authentication:
    - Session-based auth for web/browser clients (backward compatible)
    - API key auth for desktop POS app (stateless, no expiry)
    """
    source = "havano_pos"

    # =========================================================================
    # RESPONSE HELPERS
    # =========================================================================

    def _json_response(self, payload, status=200):
        """Standard JSON response wrapper."""
        return Response(
            json.dumps(payload, default=str),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def _success(self, data=None, message="", status=200):
        """Standard success response."""
        company_ctx = self._get_company_context()
        payload = {
            "success": True,
            "data": data or {},
            "message": message,
            "company": company_ctx["company"],
            "allowed_companies": company_ctx["allowed_companies"],
        }
        if status != 200:
            payload["__http_status__"] = status
        return payload

    def _error(self, error, code=400, status=None, data=None):
        """Standard error response."""
        company_ctx = self._get_company_context()
        error_text = str(error)
        payload = {
            "success": False,
            "data": data if data is not None else {},
            "message": error_text,
            "error": error_text,
            "code": code,
            "company": company_ctx["company"],
            "allowed_companies": company_ctx["allowed_companies"],
        }
        http_status = status if status is not None else code
        if http_status != 200:
            payload["__http_status__"] = http_status
        return payload

    def _normalize_api_payload(self, result):
        """Ensure every API response has success, data, and message keys."""
        if not isinstance(result, dict):
            return result
        if "success" not in result:
            return self._success(result)
        if "data" not in result:
            result["data"] = {}
        if "message" not in result:
            result["message"] = ""
        if not result.get("success") and result.get("data") is None:
            result["data"] = {}
        return result

    def _respond(self, payload, default_status=200):
        """Return JSON HTTP response from a standard API payload dict."""
        payload = self._normalize_api_payload(payload)
        status = default_status
        if isinstance(payload, dict) and "__http_status__" in payload:
            status = int(payload.pop("__http_status__"))
        return request.make_json_response(payload, status=status)

    def _inventory_order_fields(self, product_record):
        """Order 1–5 flags from havano_all_in_one (template or variant)."""
        if product_record._name == "product.template":
            template = product_record
        else:
            template = product_record.product_tmpl_id
        if "order_1" not in template._fields:
            return {"inventory_orders_enabled": False}
        enabled = bool(template.env.company.hao_activate_inventory_orders)
        if not enabled:
            return {
                "inventory_orders_enabled": False,
                "order_1": False,
                "order_2": False,
                "order_3": False,
                "order_4": False,
                "order_5": False,
            }
        return {
            "inventory_orders_enabled": True,
            "order_1": bool(template.order_1),
            "order_2": bool(template.order_2),
            "order_3": bool(template.order_3),
            "order_4": bool(template.order_4),
            "order_5": bool(template.order_5),
        }

    def _get_company_context(self):
        """Return current company context for multi-company aware clients."""
        company = None
        allowed_companies = []
        try:
            env = request.env
            current = env.company
            if current:
                company = {"id": current.id, "name": current.name}

            if request.session.uid:
                user = env.user.sudo()
                allowed_companies = [
                    {"id": comp.id, "name": comp.name}
                    for comp in user.company_ids
                ]
        except Exception:
            pass

        return {"company": company, "allowed_companies": allowed_companies}

    # =========================================================================
    # REQUEST PARSING
    # =========================================================================

    def _parse_json_data(self):
        """Parse JSON body from request (supports POS jsonrpc-style wrappers)."""
        try:
            raw_data = request.httprequest.get_data(cache=False, as_text=True) or "{}"
            data = json.loads(raw_data) if raw_data.strip() else {}
        except Exception as exc:
            _logger.warning(
                "Invalid JSON payload on %s: %s body=%r",
                request.httprequest.path,
                exc,
                (raw_data[:500] if "raw_data" in dir() else ""),
            )
            raise ValidationError(_("Invalid JSON payload.")) from exc

        if isinstance(data, dict):
            if "params" in data and isinstance(data["params"], dict):
                data = data["params"]
            elif "params" in data and isinstance(data["params"], list) and data["params"]:
                first = data["params"][0]
                if isinstance(first, dict):
                    data = first
            if (
                isinstance(data.get("data"), dict)
                and "lines" not in data
                and any(k in data.get("data", {}) for k in ("lines", "bundle_lines"))
            ):
                data = {**data, **data["data"]}
        return data

    def _get_param(self, key, default=None, cast=None):
        """Get parameter from query string or JSON body."""
        value = request.params.get(key, default)
        if cast and value is not None:
            try:
                value = cast(value)
            except (ValueError, TypeError):
                pass
        return value

    # =========================================================================
    # AUTHENTICATION - HYBRID (Session + API Key)
    # =========================================================================

    def _get_user_from_api_key(self):
        """
        Authenticate user via API key header using Odoo 19's res.users.apikeys.
        """
        api_key = (
            request.httprequest.headers.get("X-API-Key") or 
            request.httprequest.headers.get("Authorization", "").replace("Bearer ", "").strip()
        )
        
        if not api_key:
            return None
        
        try:
            # Use Odoo's built-in _check_credentials method
            uid = request.env['res.users.apikeys'].sudo()._check_credentials(
                scope='rpc',
                key=api_key
            )
            
            if uid:
                # Get the user with full access
                user = request.env['res.users'].sudo().browse(uid)
                if user and user.active:
                    _logger.info("API Key Auth: User %s (id=%s)", user.name, user.id)
                    return user
            
            _logger.warning("Invalid API key attempt")
            return None
            
        except Exception as exc:
            _logger.warning("API key validation failed: %s", str(exc))
            return None

    def _ensure_authenticated(self):
        """
        Ensure user is authenticated via EITHER:
        1. API Key (preferred for POS app - stateless, no expiry)
        2. Session (for web clients - backward compatible)
        
        Returns request.env with authenticated user.
        Raises AccessDenied if authentication fails.
        """
        # Try API Key first (stateless, no expiry)
        user = self._get_user_from_api_key()
        if user:
            # CRITICAL FIX: Set session and environment properly
            request.session.uid = user.id
            # Force the environment to use this user
            request.env = request.env(user=user.id)
            _logger.info("API Key Auth succeeded for %s with full permissions", user.name)
            return request.env
        
        # Fall back to session authentication (for web clients)
        if request.session.uid:
            user = request.env['res.users'].browse(request.session.uid)
            if user and user.active:
                _logger.debug("Session Auth: User %s (id=%s)", user.name, user.id)
                return request.env
        
        raise AccessDenied(
            _("Unauthorized. Use one of these methods:\n"
              "1. X-API-Key header with an API key (for POS app)\n"
              "2. Session cookie (login via POST /api/v1/auth/login)")
        )

    # =========================================================================
    # ROUTE HANDLER
    # =========================================================================

    def _handle_route(self, handler):
        """
        Universal route handler for all route types.
        Always returns proper JSON response.
        """
        try:
            env = self._ensure_authenticated()
            result = handler(env)
            if isinstance(result, Response):
                return result
            return self._respond(result)
        except AccessDenied as exc:
            return self._respond(
                self._error(str(exc) or "Unauthorized.", code=401, status=401),
                default_status=401,
            )
        except ValidationError as exc:
            path = request.httprequest.path
            method = request.httprequest.method
            _logger.warning(
                "API validation error %s %s: %s",
                method,
                path,
                exc,
            )
            return self._respond(
                self._error(
                    str(exc),
                    code=400,
                    status=400,
                    data={
                        "path": path,
                        "method": method,
                    },
                ),
                default_status=400,
            )
        except MissingError as exc:
            return self._respond(
                self._error(str(exc), code=404, status=404),
                default_status=404,
            )
        except Exception as exc:
            _logger.exception("API request failed: %s", exc)
            return self._respond(
                self._error(
                    "Internal server error. Check server logs.",
                    code=500,
                    status=500,
                ),
                default_status=500,
            )


def api_route(route, methods=None, auth="public", csrf=False):
    """Decorator for API routes."""
    if methods is None:
        methods = ["GET"]
    
    def decorator(func):
        @wraps(func)
        def wrapper(self, **kwargs):
            return func(self, **kwargs)
        return wrapper
    return decorator