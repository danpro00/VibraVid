# 03.07.26

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class EquivalentCommandBuilder:
    _CONTEXT_TRACKER_FLAGS = (
        ("cli_search", "-s"),
        ("cli_item", "--item"),
        ("cli_season_selection", "--season"),
        ("cli_episode_selection", "--episode"),
    )

    def __init__(self, excluded_dests: Iterable[str], program_name: str = "manual.py"):
        """
        Args:
            excluded_dests: dest da NON riproporre come flag standard perché già
                gestiti altrove (context_tracker) o non pertinenti a questo comando
                (es. --down, --update, --dep).
            program_name: nome dello script stampato all'inizio del comando.
        """
        self._excluded_dests = set(excluded_dests)
        self._program_name = program_name

    def build(self, args, parser, context_tracker, site_option_dests: Iterable[str] = None) -> str | None:
        """Build the equivalent command line for the given args and context_tracker, or None if no site is set."""
        site = getattr(context_tracker, "cli_site", None)
        if not site:
            return None

        parts = ["python", self._program_name, "--site", str(site)]
        parts += self._context_tracker_parts(context_tracker)
        parts += self._standard_flag_parts(args, parser, site_option_dests)
        parts += self._site_option_parts(context_tracker)

        return " ".join(parts)

    def log_equivalent_command(self, args, parser, context_tracker, site_option_dests: Iterable[str] = None) -> None:
        """Build and log the equivalent command line for the given args and context_tracker, if a site is set."""
        equivalent_cmd = self.build(args, parser, context_tracker, site_option_dests)
        if equivalent_cmd:
            logger.info(f"Equivalent command: {equivalent_cmd}")

    def _context_tracker_parts(self, context_tracker) -> list[str]:
        parts = []
        for attr, flag in self._CONTEXT_TRACKER_FLAGS:
            value = getattr(context_tracker, attr, None)
            if value is None or value == "":
                continue
            parts += [flag, self._quote_if_needed(value)]
        return parts

    def _standard_flag_parts(self, args, parser, site_option_dests: Iterable[str] = None) -> list[str]:
        site_dests = set(site_option_dests or ())
        parts = []
        for action in parser._actions:
            dest = action.dest
            if dest in self._excluded_dests or dest in site_dests or not action.option_strings:
                continue
            default = action.default
            value = getattr(args, dest, default)
            if value == default:
                continue
            parts += self._flag_and_value(action.option_strings[-1], value)
        return parts

    def _site_option_parts(self, context_tracker) -> list[str]:
        parts = []
        for dest, value in (getattr(context_tracker, "site_options", None) or {}).items():
            if dest == "drm" or value in (None, False):
                continue
            flag = "--" + dest.replace("_", "-")
            parts += self._flag_and_value(flag, value)
        return parts

    @staticmethod
    def _flag_and_value(flag: str, value: Any) -> list[str]:
        if value is True:
            return [flag]
        return [flag, EquivalentCommandBuilder._quote_if_needed(value)]

    @staticmethod
    def _quote_if_needed(value: Any) -> str:
        text = str(value)
        return f'"{text}"' if " " in text else text