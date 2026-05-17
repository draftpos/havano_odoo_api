# controllers/helpers.py
import logging
from functools import wraps
from odoo import _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


def safe_field_get(record, field_name, default=None):
    """Safely get a field value, return default if field doesn't exist."""
    try:
        if hasattr(record, field_name):
            return record[field_name]
        return default
    except Exception:
        return default


def safe_field_exists(model, field_name):
    """Check if a field exists in a model."""
    try:
        return field_name in model._fields
    except Exception:
        return False


def log_and_raise(message, level='error'):
    """Log message and raise ValidationError."""
    if level == 'error':
        _logger.error(message)
    else:
        _logger.warning(message)
    raise ValidationError(_(message))


def idempotent_check(model, field, value, pos_reference, model_name):
    """Check for existing record to ensure idempotency."""
    existing = model.search([(field, '=', value)], limit=1)
    if existing:
        _logger.info(f"Idempotent check: {model_name} already exists with {field}={value}")
        return existing
    return None


def safe_search(env, model_name, domain, limit=100):
    """Safely search a model, return empty list if model doesn't exist."""
    try:
        return env[model_name].search(domain, limit=limit)
    except Exception as e:
        _logger.warning(f"Cannot search {model_name}: {str(e)}")
        return env[model_name].browse()


def safe_create(env, model_name, values):
    """Safely create a record, handle missing fields gracefully."""
    try:
        # Filter out fields that don't exist in the model
        model = env[model_name]
        valid_fields = set(model._fields.keys())
        filtered_values = {k: v for k, v in values.items() if k in valid_fields}
        return model.create(filtered_values)
    except Exception as e:
        _logger.error(f"Failed to create {model_name}: {str(e)}")
        raise ValidationError(_("Failed to create record: %s") % str(e))