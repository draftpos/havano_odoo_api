# controllers/auth.py - Complete Odoo 19 compatible version

from odoo import http
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)

class HavanoAuthController(http.Controller):
    def _company_context(self):
        company = None
        allowed_companies = []
        if request.session.uid:
            user = request.env["res.users"].sudo().browse(request.session.uid)
            if user.exists():
                company = {"id": user.company_id.id, "name": user.company_id.name}
                allowed_companies = [
                    {"id": comp.id, "name": comp.name}
                    for comp in user.company_ids
                ]
        return {"company": company, "allowed_companies": allowed_companies}
    
    def _get_user_warehouses(self, env):
        warehouses = []
        try:
            warehouse_model = env["stock.warehouse"].sudo()
            all_warehouses = warehouse_model.search_read(
                domain=[],
                fields=["id", "name", "code", "company_id", "partner_id", "active"],
                order="id",
            )
            for wh in all_warehouses:
                warehouse = warehouse_model.browse(wh["id"])
                wh["location_id"] = warehouse.lot_stock_id.id
                wh["location_name"] = warehouse.lot_stock_id.name
                wh["in_type_id"] = warehouse.in_type_id.id
                wh["out_type_id"] = warehouse.out_type_id.id
            warehouses = all_warehouses
        except Exception as e:
            _logger.warning("Could not fetch warehouses: %s", str(e))
        return warehouses
    
    def _generate_api_key_for_user(self, user_id, name="POS App"):
        """
        Generate API key using Odoo 19's native method.
        Based on res.users.apikeys._generate() from base/models/res_users.py
        """
        try:
            user = request.env['res.users'].sudo().browse(user_id)
            if not user:
                _logger.error("User not found: %s", user_id)
                return None
            
            ApiKey = request.env['res.users.apikeys'].sudo()
            
            # Delete existing keys
            existing_keys = ApiKey.search([
                ('user_id', '=', user_id),
                ('name', '=', name)
            ])
            if existing_keys:
                existing_keys._remove()
                _logger.info("Removed existing API keys for user %s", user.name)
            
            # Generate new API key using Odoo 19's _generate method
            # Parameters: scope, name, expiration_date
            # scope=None means access to any RPC call
            api_key = ApiKey._generate(
                scope=None,
                name=name,
                expiration_date=None  # Persistent key (no expiration)
            )
            
            if api_key:
                _logger.info("API Key generated for user %s", user.name)
                return api_key
            else:
                _logger.warning("API key generation failed")
                return None
                
        except Exception as exc:
            _logger.error("Failed to generate API key: %s", str(exc))
            return None
    
    @http.route("/api/v1/auth/login", auth="public", methods=["POST"], type="http", csrf=False)
    def login(self, **kwargs):
        try:
            data = json.loads(request.httprequest.data.decode())
            if "params" in data:
                data = data["params"]
        except:
            data = {}
        
        db = data.get("db", request.env.cr.dbname)
        login = data.get("login")
        password = data.get("password")
        generate_api_key = data.get("generate_api_key", True)
        
        if not login or not password:
            company_ctx = self._company_context()
            return request.make_json_response({
                "success": False,
                "error": "Login and password are required.",
                "code": 400,
                "company": company_ctx["company"],
                "allowed_companies": company_ctx["allowed_companies"],
            }, status=400)
        
        try:
            request.session.db = db
            credentials = {
                "login": login,
                "password": password,
                "type": "password",
            }
            
            auth_info = request.session.authenticate(request.env, credentials)
            uid = auth_info.get("uid")
            
            if not uid:
                company_ctx = self._company_context()
                return request.make_json_response({
                    "success": False,
                    "error": "Invalid credentials.",
                    "code": 401,
                    "company": company_ctx["company"],
                    "allowed_companies": company_ctx["allowed_companies"],
                }, status=401)
            
            request.session.uid = uid
            request.session.login = login
            
            user = request.env['res.users'].sudo().browse(uid)
            _logger.info("LOGIN SUCCESS: %s (id=%s)", user.name, user.id)
            
            # Generate API key if requested
            api_key = None
            if generate_api_key:
                api_key = self._generate_api_key_for_user(uid, f"POS App - {user.name}")
                if api_key:
                    _logger.info("API Key generated for user %s", user.name)
                else:
                    _logger.warning("API key generation failed")
            
            company_ctx = self._company_context()
            warehouses = self._get_user_warehouses(request.env)
            
            response_data = {
                "user_id": user.id,
                "user_name": user.name,
                "user_email": user.email,
                "company_id": user.company_id.id,
                "company_name": user.company_id.name,
                "session_id": request.session.sid,
                "database": db,
                "warehouses": warehouses,
            }
            
            if api_key:
                response_data["api_key"] = api_key
                response_data["api_key_note"] = "Save this key! Use in X-API-Key header"
            else:
                response_data["api_key_note"] = "Use session cookie for authentication"
            
            return request.make_json_response({
                "success": True,
                "data": response_data,
                "message": "Login successful" + (" (API key generated)" if api_key else ""),
                "company": company_ctx["company"],
                "allowed_companies": company_ctx["allowed_companies"],
            })
            
        except Exception as exc:
            _logger.error("Login failed: %s", str(exc))
            company_ctx = self._company_context()
            return request.make_json_response({
                "success": False,
                "error": "Invalid credentials.",
                "code": 401,
                "company": company_ctx["company"],
                "allowed_companies": company_ctx["allowed_companies"],
            }, status=401)