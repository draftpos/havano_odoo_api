# models/product_template.py
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
import re


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.constrains('name')
    def _check_unique_name(self):
        """Prevent duplicate product names (case and space insensitive)"""
        for product in self:
            if not product.name:
                continue
            
            # Normalize name: lowercase, remove extra spaces, strip
            normalized_name = re.sub(r'\s+', ' ', product.name.strip()).lower()
            
            # Check for existing products with same normalized name
            existing = self.search([
                ('id', '!=', product.id),
                ('name', '!=', False)
            ])
            
            for existing_product in existing:
                existing_normalized = re.sub(r'\s+', ' ', existing_product.name.strip()).lower()
                if normalized_name == existing_normalized:
                    raise ValidationError(_(
                        "Product with name '%s' already exists. Please use a different name."
                        % existing_product.name
                    ))

    @api.constrains('default_code')
    def _check_unique_default_code(self):
        """Ensure ITEM CODE is unique"""
        for product in self:
            if product.default_code:
                existing = self.search([
                    ('id', '!=', product.id),
                    ('default_code', '=', product.default_code)
                ])
                if existing:
                    raise ValidationError(_(
                        "ITEM CODE '%s' is already used by product '%s'. Please use a unique code."
                        % (product.default_code, existing[0].name)
                    ))

    @api.model
    def create(self, vals):
        """Auto-generate ITEM CODE if not provided, starting from 101"""
        if not vals.get('default_code'):
            # Try to get next sequence value
            sequence = self.env['ir.sequence'].next_by_code('product.item.code')
            if sequence:
                vals['default_code'] = sequence
            else:
                # Fallback: get max existing numeric code
                products = self.search([('default_code', '!=', False)], order='default_code desc', limit=1)
                if products and products[0].default_code and products[0].default_code.isdigit():
                    next_code = int(products[0].default_code) + 1
                else:
                    next_code = 101
                vals['default_code'] = str(next_code)
        
        return super().create(vals)

    @api.model_create_multi
    def create(self, vals_list):
        """Override to ensure sequence is used for multiple records"""
        for vals in vals_list:
            if not vals.get('default_code'):
                sequence = self.env['ir.sequence'].next_by_code('product.item.code')
                if sequence:
                    vals['default_code'] = sequence
        return super().create(vals_list)