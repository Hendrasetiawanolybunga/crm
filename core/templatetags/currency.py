from django import template

register = template.Library()


@register.filter
def currency(value):
    """Format a number as Indonesian Rupiah string: Rp10.000,00"""
    try:
        amount = float(value)
    except Exception:
        return value
    # format with US-style thousands separator and dot decimal, then swap to Indonesian format
    s = f"{amount:,.2f}"  # e.g. '10,000.00'
    # swap: comma -> temporary, dot -> comma, temporary -> dot
    s = s.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f"Rp{s}"
