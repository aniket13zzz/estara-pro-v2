"""Print the WhatsApp templates to submit to Meta for approval:
``python -m app.channels.whatsapp.templates``.

Each file in ``client/scripts/<lang>/whatsapp/`` is one template: the file name is the template
name, the language folder is its language, and each {{placeholder}} becomes {{1}}, {{2}}, ... in
order of first appearance. Meta asks for sample values for each parameter; examples are printed.
"""

from __future__ import annotations

from app.channels.whatsapp.service import meta_template_text, template_keys
from app.core.config import WHATSAPP_TEMPLATE_DIR, load_config

SAMPLES = {
    "customer_name": "Asha",
    "company_name": "Sunrise Developers",
    "agent_name": "Priya",
    "project_name": "Sunrise Greens",
    "visit_date": "Saturday 7 November",
    "visit_time": "11 am",
    "salesperson_name": "Rahul",
}


def main() -> int:
    config = load_config()
    for language in config.agent.languages:
        folder = config.scripts_root / language / WHATSAPP_TEMPLATE_DIR
        for path in sorted(folder.glob("*.md")):
            name = path.stem
            keys = template_keys(config, language, name)
            print(f"Template name: {name}    Language: {language}    Category: Utility")
            print(meta_template_text(config, language, name))
            for i, key in enumerate(keys, start=1):
                print(f"  {{{{{i}}}}} = {key} (sample: {SAMPLES.get(key, key)})")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
