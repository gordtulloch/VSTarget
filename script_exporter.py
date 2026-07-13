"""iTelescope / ACP observing script generator."""
from __future__ import annotations


from models import ObservingTarget


class ScriptExporter:
    """Generates iTelescope ACP-format observing plan files.

    Script format reference:
      https://support.itelescope.net/support/solutions/articles/143037

    Target line format (tab-delimited):
      <name>  <RA decimal hours>  <Dec decimal degrees>

    Directives are placed immediately before each target.  Global directives
    (#defocus, #vphot, etc.) appear at the top of the file.
    """

    def generate(
        self,
        targets: list[ObservingTarget],
        defocus: bool = False,
        vphot: bool = False,
        platesolve: bool = False,
        filteroffsets: bool = False,
    ) -> str:
        """Return the complete script as a string.

        Targets are sorted ascending by RA (as required for an efficient
        east-to-west observing run).
        """
        lines: list[str] = []

        # ── Global directives ────────────────────────────────────────────────
        global_directives = []
        if defocus:
            global_directives.append("#defocus")
        if vphot:
            global_directives.append("#vphot")
        if platesolve:
            global_directives.append("#platesolve")
        if filteroffsets:
            global_directives.append("#filteroffsets")

        if global_directives:
            lines.extend(global_directives)
            lines.append("")

        # ── Per-target blocks, sorted by RA ──────────────────────────────────
        sorted_targets = sorted(targets, key=lambda t: t.aavso.ra)

        for ot in sorted_targets:
            lines.append(f"#filter {ot.script_filters}")
            lines.append(f"#count {ot.script_counts}")
            lines.append(f"#interval {ot.script_intervals}")
            lines.append(f"#binning {ot.script_binning}")
            # Tab-separated: name, RA in decimal hours, Dec in decimal degrees
            lines.append(
                f"{ot.aavso.star_name}\t"
                f"{ot.aavso.ra_hours:.10f}\t"
                f"{ot.aavso.dec:.10f}"
            )
            lines.append("")  # blank line between targets

        return "\n".join(lines)

    def validate(self, targets: list[ObservingTarget]) -> list[str]:
        """Return a list of human-readable validation warnings.

        ACP requires that #filter, #count, #interval, and #binning all have
        the same number of comma-separated values.
        """
        warnings: list[str] = []
        for ot in targets:
            name = ot.aavso.star_name
            parts = {
                "filter": [x.strip() for x in ot.script_filters.split(",") if x.strip()],
                "count": [x.strip() for x in ot.script_counts.split(",") if x.strip()],
                "interval": [x.strip() for x in ot.script_intervals.split(",") if x.strip()],
                "binning": [x.strip() for x in ot.script_binning.split(",") if x.strip()],
            }
            lengths = {k: len(v) for k, v in parts.items()}
            unique_lengths = set(lengths.values())
            if len(unique_lengths) != 1:
                detail = ", ".join(f"{k}={n}" for k, n in lengths.items())
                warnings.append(
                    f"{name}: filter/count/interval/binning must have the same "
                    f"number of values ({detail})"
                )
            for key, values in parts.items():
                if not values:
                    warnings.append(f"{name}: {key} is empty")

        return warnings
