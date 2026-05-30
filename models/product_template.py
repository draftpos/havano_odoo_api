from odoo import models

# ITEM CODE generation, uniqueness checks, and labeling are provided by
# havano_all_in_one. This module only adds API-specific product views.


class ProductTemplate(models.Model):
    _inherit = "product.template"
