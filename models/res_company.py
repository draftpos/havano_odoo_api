# models/res_company.py
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def set_tax_inclusive_default(self):
        """Set tax calculation to inclusive on module install"""
        companies = self.search([('tax_calculation_rounding_method', '!=', 'tax_inclusive')])
        if companies:
            companies.write({'tax_calculation_rounding_method': 'tax_inclusive'})
            _logger.info("Set tax calculation to inclusive for %s companies", len(companies))
        return True