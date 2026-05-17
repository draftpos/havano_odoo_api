# models/res_users.py
from odoo import fields, models, api


class ResUsers(models.Model):
    _inherit = 'res.users'

    # POS workflow preferences - Master toggle
    pos_enable_auto_workflow = fields.Boolean(
        string='Enable Automatic POS Workflow',
        default=False,
        help='Master toggle for automatic POS workflow. If disabled, all auto options below are ignored.'
    )

    # POS workflow preferences - Individual toggles
    pos_auto_confirm_sale = fields.Boolean(
        string='Auto Confirm Sales Orders',
        default=False,
        help='Automatically confirm sales orders from POS (convert quotation to sales order)'
    )

    pos_auto_create_invoice = fields.Boolean(
        string='Auto Create Invoice',
        default=False,
        help='Automatically create invoice from sales order'
    )

    pos_auto_post_invoice = fields.Boolean(
        string='Auto Post Invoice',
        default=False,
        help='Automatically post/validate invoices (makes them final)'
    )

    pos_auto_validate_delivery = fields.Boolean(
        string='Auto Validate Delivery',
        default=False,
        help='Automatically validate delivery orders (complete the shipment)'
    )

    pos_auto_register_payment = fields.Boolean(
        string='Auto Register Payment',
        default=False,
        help='Automatically register and reconcile payments with invoices'
    )

    # Helper method to get workflow configuration
    def get_pos_workflow_config(self):
        """Return POS workflow configuration for the user."""
        self.ensure_one()
        return {
            'enabled': self.pos_enable_auto_workflow,
            'auto_confirm_sale': self.pos_enable_auto_workflow and self.pos_auto_confirm_sale,
            'auto_create_invoice': self.pos_enable_auto_workflow and self.pos_auto_create_invoice,
            'auto_post_invoice': self.pos_enable_auto_workflow and self.pos_auto_create_invoice and self.pos_auto_post_invoice,
            'auto_validate_delivery': self.pos_enable_auto_workflow and self.pos_auto_validate_delivery,
            'auto_register_payment': self.pos_enable_auto_workflow and self.pos_auto_register_payment,
        }

    @api.model
    def get_current_user_workflow_config(self):
        """Get workflow config for current user."""
        return self.env.user.get_pos_workflow_config()