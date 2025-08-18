from django import template
from django.utils.safestring import mark_safe
import json

register = template.Library()

@register.filter
def extract_tag_terms(tags):
    """Extract 'term' values from tag dictionaries."""
    if not tags:
        return []
    
    terms = []
    for tag in tags:
        if isinstance(tag, dict):
            # Extract 'term' from dictionary
            term = tag.get('term', '')
            if term:
                terms.append(term)
        elif isinstance(tag, str):
            # Already a string, use as-is
            terms.append(tag)
    
    return terms

@register.filter
def extract_tag_term(tag):
    """Extract 'term' value from a single tag dictionary."""
    if isinstance(tag, dict):
        return tag.get('term', str(tag))
    return str(tag)

@register.filter
def join_tag_terms(tags, separator=", "):
    """Extract terms from tags and join them."""
    terms = extract_tag_terms(tags)
    return separator.join(terms) if terms else "None"