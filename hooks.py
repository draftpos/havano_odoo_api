# hooks.py
import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Post-installation hook"""
    _logger.info("Post-install hook completed")