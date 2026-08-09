"""Minimal Prometheus text-format registry.

Samples are accumulated per metric name so HELP/TYPE are emitted exactly once even
though collectors run once per zone, and duplicate label sets are dropped (they would
make the exposition invalid).
"""


def escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_labels(labels):
    return ",".join(f'{k}="{escape(v)}"' for k, v in sorted(labels.items()))


def top(counter, limit):
    """Highest `limit` entries of a {key: number} mapping, descending."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))[:limit]


class Registry:
    def __init__(self):
        self.families = {}

    def add(self, name, help_text, metric_type, samples):
        """samples: iterable of (labels dict, value). Empty input is ignored."""
        samples = list(samples)
        if not samples:
            return
        family = self.families.setdefault(name, [help_text, metric_type, []])
        family[2].extend(samples)

    def text(self):
        lines = []
        for name, (help_text, metric_type, samples) in self.families.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {metric_type}")
            seen = set()
            for labels, value in samples:
                rendered = render_labels(labels)
                if rendered in seen:
                    continue
                seen.add(rendered)
                lines.append(f"{name}{{{rendered}}} {value}" if rendered else f"{name} {value}")
        return "\n".join(lines) + "\n"
