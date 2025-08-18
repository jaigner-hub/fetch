from django import template
from django.utils.safestring import mark_safe
import bleach

register = template.Library()

@register.filter(name='clean_html')
def clean_html(value):
    """
    Clean and sanitize HTML content while preserving safe tags and attributes.
    """
    if not value:
        return ''
    
    # Allowed tags
    allowed_tags = [
        'p', 'a', 'img', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'blockquote', 'ul', 'ol', 'li', 'strong', 'b', 'em', 'i',
        'br', 'hr', 'span', 'div', 'figure', 'figcaption',
        'code', 'pre', 'table', 'thead', 'tbody', 'tr', 'th', 'td'
    ]
    
    # Allowed attributes
    allowed_attrs = {
        'a': ['href', 'title', 'target', 'rel'],
        'img': ['src', 'alt', 'title', 'width', 'height', 'loading'],
        'blockquote': ['cite'],
        'code': ['class'],
        'pre': ['class'],
        'span': ['class'],
        'div': ['class'],
        'figure': ['class'],
        'table': ['class'],
        'td': ['colspan', 'rowspan'],
        'th': ['colspan', 'rowspan']
    }
    
    # Clean the HTML
    cleaned = bleach.clean(
        value,
        tags=allowed_tags,
        attributes=allowed_attrs,
        strip=True
    )
    
    # Linkify plain URLs
    cleaned = bleach.linkify(cleaned, callbacks=[target_blank])
    
    return mark_safe(cleaned)


def target_blank(attrs, new=False):
    """
    Callback to add target="_blank" and rel="noopener noreferrer" to links.
    """
    attrs[(None, 'target')] = '_blank'
    attrs[(None, 'rel')] = 'noopener noreferrer'
    return attrs