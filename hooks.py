# hooks.py
import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Post-installation hook to set tax inclusive by default"""
    
    # Set tax inclusive for all companies
    companies = env['res.company'].search([])
    for company in companies:
        if hasattr(company, 'tax_calculation_rounding_method') and company.tax_calculation_rounding_method != 'tax_inclusive':
            company.tax_calculation_rounding_method = 'tax_inclusive'
            _logger.info("Set tax inclusive for company: %s", company.name)
    
    _logger.info("Post-install hook completed: Tax Inclusive set for all companies")