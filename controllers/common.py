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
        return {
            "success": True,
            "data": data or {},
            "message": message,
            "company": company_ctx["company"],
            "allowed_companies": company_ctx["allowed_companies"],
        }

    def _error(self, error, code=400, status=None):
        """Standard error response."""
        company_ctx = self._get_company_context()
        return {
            "success": False,
            "error": str(error),
            "code": code,
            "company": company_ctx["company"],
            "allowed_companies": company_ctx["allowed_companies"],
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
        """Parse JSON body from request."""
        try:
            raw_data = request.httprequest.get_data(cache=False, as_text=True) or "{}"
            return json.loads(raw_data)
        except Exception as exc:
            _logger.warning("Invalid JSON payload: %s", exc)
            raise ValidationError(_("Invalid JSON payload.")) from exc

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
            if hasattr(request, 'make_json_response'):
                return request.make_json_response(result)
            return result
        except AccessDenied as exc:
            return request.make_json_response(
                {"success": False, "error": str(exc) or "Unauthorized.", "code": 401},
                status=401,
            )
        except ValidationError as exc:
            return request.make_json_response(
                {"success": False, "error": str(exc), "code": 400},
                status=400,
            )
        except MissingError as exc:
            return request.make_json_response(
                {"success": False, "error": str(exc), "code": 404},
                status=404,
            )
        except Exception as exc:
            _logger.exception("API request failed: %s", exc)
            return request.make_json_response(
                {"success": False, "error": "Internal server error. Check server logs.", "code": 500},
                status=500,
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