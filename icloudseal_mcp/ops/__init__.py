"""Optional ops helpers. Generate only; never load LaunchAgents unless asked."""

from .cleanup_agent import generate_mail_cleanup_plist, write_mail_cleanup_plist

__all__ = ["generate_mail_cleanup_plist", "write_mail_cleanup_plist"]
