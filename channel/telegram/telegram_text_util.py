import re

def escape(text):
    """Escapes special characters for Telegram MarkdownV2."""
    escape_chars = r'\*_[]()~`>#+-=|{}.!'
    return re.sub(r'([' + re.escape(escape_chars) + r'])', r'\\\1', text)