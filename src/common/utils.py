import json
import os

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup


def _templates_path() -> str:
    # src/common → src/app/templates
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "templates"))


_env = Environment(
    loader=FileSystemLoader(_templates_path()),
    autoescape=select_autoescape(["html", "xml"]),
    enable_async=False,
)
_env.filters["tojson"] = lambda v: Markup(json.dumps(v, ensure_ascii=False))


def format_number(value):
    try:
        val = float(value)
        if abs(val) >= 1_000_000_000:
            return f"{val/1_000_000_000:.2f}B"
        elif abs(val) >= 1_000_000:
            return f"{val/1_000_000:.2f}M"
        elif abs(val) >= 1_000:
            return f"{val:,.0f}"
        else:
            return f"{val:,.2f}"
    except (ValueError, TypeError):
        return value


_env.filters["format_number"] = format_number


def render_template(name: str, **context) -> str:
    tpl = _env.get_template(name)
    return tpl.render(**context)
