from django import template
from django.utils.safestring import mark_safe
import json
import re

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

@register.filter
def extract_json_summary(text):
    """Extract summary from JSON string or return text as-is."""
    if not text:
        return ""
    
    text_str = str(text).strip()
    
    # Check if it's JSON
    if text_str.startswith('{'):
        try:
            parsed = json.loads(text_str)
            if isinstance(parsed, dict):
                # Try different possible keys
                return parsed.get('summary', parsed.get('text', parsed.get('content', text_str)))
            return text_str
        except (json.JSONDecodeError, ValueError):
            pass
    
    return text_str

@register.filter
def clean_html_content(text):
    """Clean HTML content for display - strip tags for plain text display."""
    if not text:
        return ""
    
    text_str = str(text)
    
    # Remove HTML tags for clean text display
    clean_text = re.sub('<[^<]+?>', '', text_str)
    # Remove excessive whitespace
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    return clean_text

@register.filter
def render_html_content(text):
    """Mark HTML content as safe for rendering."""
    if not text:
        return ""
    
    # Only mark as safe if it contains HTML tags
    if '<' in str(text) and '>' in str(text):
        return mark_safe(text)
    
    return text